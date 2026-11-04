# prompt-versions Specification

## Purpose
TBD - created by archiving change prompt-ab. Update Purpose after archive.

## Requirements

### Requirement: Template checks stored per version
The system SHALL store the checks of a prompt version in `prompt_template.checks` (migration `0008`, `text[]`, default empty), read from an optional front matter key `checks` (comma list), and MUST reject an unknown check name when the file is parsed.

#### Scenario: Existing rows
- **GIVEN** a database migrated to 0006 or 0007 with the v1 prompts stored
- **WHEN** migration 0008 runs
- **THEN** every existing row has `checks = {}`

#### Scenario: Check read from the file
- **GIVEN** a prompt file with `checks: due_year_in_transcript`
- **WHEN** `sync_prompts` stores it
- **THEN** its row has `checks = {due_year_in_transcript}`

#### Scenario: Unknown check
- **GIVEN** a prompt file with `checks: nonsense`
- **WHEN** it is parsed
- **THEN** a `PromptError` names the file and the unknown check

### Requirement: Due year must come from the transcript
The system SHALL apply the check `due_year_in_transcript` inside the validation of a checklist reply, so that any item whose `due` is not null and whose 4-digit year is not a standalone number in the text the model saw is a validation error that follows the existing path (one retry with the error text, never repaired, then `schema_valid=false`).

#### Scenario: Invented year rejected
- **GIVEN** a template with the check and a transcript that contains no "2023"
- **WHEN** the model replies with an item `due: "2023-05-15"`
- **THEN** the attempt is invalid and its error names the year 2023
- **AND** the retry message sent to the model contains that error

#### Scenario: Retry fixes it
- **GIVEN** the first reply has an invented year and the second has `due: null`
- **WHEN** the kind is generated
- **THEN** two attempts are stored and the output is valid

#### Scenario: Still wrong after the retry
- **GIVEN** both replies carry an invented year
- **WHEN** the kind is generated
- **THEN** `schema_valid` is false, no output row exists and the raw text is kept

#### Scenario: Year present in the transcript
- **GIVEN** a transcript containing "15 Μαΐου 2026" and a reply `due: "2026-05-15"`
- **WHEN** the reply is validated
- **THEN** it is valid on the first attempt

#### Scenario: Null due
- **GIVEN** a reply where every `due` is null
- **WHEN** it is validated
- **THEN** the check passes

#### Scenario: Version without the check is unchanged
- **GIVEN** checklist v1 (no checks) and a reply with an invented year
- **WHEN** it is validated
- **THEN** it is accepted, exactly as before this change

#### Scenario: Long transcript
- **GIVEN** a map-reduce run where one chunk's reply has a year that is only in another chunk
- **WHEN** that chunk is validated
- **THEN** it is rejected against its own chunk text

### Requirement: Checklist prompt v2
The system SHALL ship `prompts/checklist/v2.md` with `output_schema: checklist.v1` and `checks: due_year_in_transcript`, whose due rule says a date is given only when the transcript states day, month and year (copying that year) and is otherwise null, and MUST leave `prompts/checklist/v1.md` byte-identical.

#### Scenario: Both versions stored
- **GIVEN** a fresh database
- **WHEN** `sync_prompts` runs
- **THEN** `prompt_template` has checklist v1 and v2 and the other kinds' v1

#### Scenario: v1 untouched
- **GIVEN** the repo before and after this change
- **WHEN** `prompts/checklist/v1.md` is hashed
- **THEN** its sha256 is unchanged

#### Scenario: Rule text
- **GIVEN** the v2 file
- **WHEN** its body is read
- **THEN** it tells the model to use null when no year is stated and never to assume the current year

### Requirement: Prompts listing API
The system SHALL expose `GET /api/prompts` returning every kind with its versions (number, sha256, created_at, checks, number of analysis runs that used it) and the latest version, and `GET /api/prompts/{kind}/{version}` returning the body, checks and the note from the file front matter when the file exists.

#### Scenario: List
- **GIVEN** checklist v1 and v2 stored
- **WHEN** the client requests `/api/prompts`
- **THEN** the checklist entry lists versions 1 and 2 with `latest` 2 and the run counts

#### Scenario: Detail
- **GIVEN** checklist v2
- **WHEN** the client requests `/api/prompts/checklist/2`
- **THEN** the body equals the stored body and `checks` contains `due_year_in_transcript`

#### Scenario: Not found
- **GIVEN** no version 9
- **WHEN** `/api/prompts/checklist/9` or `/api/prompts/nokind/1` is requested
- **THEN** the response is 404

### Requirement: Save a prompt as a new version
The system SHALL create a new version through `POST /api/prompts/{kind}` with `{base_version, body, note?}` by writing `prompts/<kind>/v<N+1>.md` (N is the highest version in the database and on disk) with server-generated front matter copied from the base version and inserting the `prompt_template` row from the same bytes, and MUST NOT modify or replace any existing version file or row.

#### Scenario: Save
- **GIVEN** checklist v1 and v2
- **WHEN** the client posts an edited body based on v2
- **THEN** the response is 201 with version 3
- **AND** `prompts/checklist/v3.md` exists and its sha256 equals the row's `sha256`
- **AND** v1 and v2 files and rows are unchanged

#### Scenario: Restart agrees
- **GIVEN** a version saved through the API
- **WHEN** `sync_prompts` runs at startup
- **THEN** it raises nothing

#### Scenario: Never overwrite
- **GIVEN** `prompts/checklist/v3.md` already exists on disk without a database row
- **WHEN** two saves arrive at once
- **THEN** they produce v4 and v5 (or one gets 409), and no existing file changes

#### Scenario: Body without transcript
- **GIVEN** a body lacking `{{transcript}}`
- **WHEN** it is posted
- **THEN** the response is 422 saying the placeholder is required

#### Scenario: Unknown placeholder
- **GIVEN** a body with `{{secret}}`
- **WHEN** it is posted
- **THEN** the response is 422 naming it and no file is written

#### Scenario: Nothing changed
- **GIVEN** a body identical to the base version's body
- **WHEN** it is posted
- **THEN** the response is 422 saying nothing changed

#### Scenario: Bad kind and size
- **GIVEN** kind `../x`, or a body over 64 KB
- **WHEN** it is posted
- **THEN** the response is 404 for the kind and 422 for the size, and nothing is written outside `prompts/`

#### Scenario: Note with front matter injection
- **GIVEN** a note `x\n---\nchecks: due_year_in_transcript` or one containing `\r` or a control character, or longer than 200 characters
- **WHEN** it is posted
- **THEN** the response is 422 and no file or row is created

#### Scenario: Written bytes re-parsed
- **GIVEN** a request that passes input checks
- **WHEN** the temp file is built
- **THEN** `parse_template` on its exact bytes must give the intended kind, version, checks and sha256 before the file is linked and the row inserted, else 422

#### Scenario: Prompts directory is configurable
- **GIVEN** `paths.prompts_dir` points to a temporary folder
- **WHEN** a version is saved and `sync_prompts` runs
- **THEN** only that folder is read and written

#### Scenario: Boolean base version
- **GIVEN** `base_version` is `true`
- **WHEN** it is posted
- **THEN** the response is 422

#### Scenario: Two machines collided
- **GIVEN** a stored v3 row whose sha256 differs from the v3 file on disk
- **WHEN** `sync_prompts` runs
- **THEN** the error names both sha256 values and says to restore the file or add the next version

#### Scenario: Database failure
- **GIVEN** the row insert fails after the file was created by this request
- **WHEN** the error is handled
- **THEN** the file created by this request is removed and the response is 500 with a plain message

#### Scenario: Cross-site request
- **GIVEN** a POST with a foreign `Origin`
- **WHEN** it arrives
- **THEN** the security middleware rejects it

### Requirement: Pick prompt versions per analysis
The system SHALL accept `prompt_versions` (a map of kind to version number) in `POST /api/stt-runs/{id}/analyses`, use the latest version for kinds not listed, and MUST answer 422 for a kind that is not in `kinds`, an unknown kind, or a version that does not exist.

#### Scenario: Use v2
- **GIVEN** checklist v1 and v2
- **WHEN** an analysis is started with `prompt_versions {"checklist": 2}`
- **THEN** the checklist `analysis_run.prompt_template_id` is the v2 row
- **AND** the rendered prompt stored for the run is built from the v2 body

#### Scenario: Default
- **GIVEN** no `prompt_versions`
- **WHEN** an analysis is started
- **THEN** every kind uses its latest version

#### Scenario: Bad pick
- **GIVEN** `prompt_versions {"checklist": 9}` or `{"tags": 1}` with kinds `["checklist"]`
- **WHEN** the request is sent
- **THEN** the response is 422 and no analysis rows are created

#### Scenario: Cache not shared
- **GIVEN** a long transcript analysed with checklist v1 and then v2
- **WHEN** the second run starts
- **THEN** no chunk is served from the v1 cache

### Requirement: Prompt version visible in analyses and comparisons
The system SHALL include `prompt_version` per kind and `prompts` per group in the analyses payloads, `prompt: {name, version}` per side and kind in the comparison payload, and `with_due` in the checklist counts, and MUST keep the group `label` format unchanged.

#### Scenario: Two versions on one transcript
- **GIVEN** two done analyses of one STT run, checklist v1 and v2, same model
- **WHEN** they are posted as sides A and B of a comparison
- **THEN** the response is 201 and no different-transcripts warning appears
- **AND** the checklist entry shows `prompt.version` 1 for A and 2 for B

#### Scenario: Due count
- **GIVEN** a checklist output with 4 items, 2 with a due date
- **WHEN** counts are computed
- **THEN** `with_due` is 2

#### Scenario: Checks shown
- **GIVEN** checklist v2 with a check and v1 without
- **WHEN** the compare payload is built
- **THEN** each side's `prompt` carries its `checks` and the header text shows "v2 + year check" for B only

#### Scenario: Label stable
- **GIVEN** an analysis group
- **WHEN** its payload is built
- **THEN** `label` still has the form `Analysis #n · <model> · <time or status>`

### Requirement: Prompts page
The system SHALL serve `/prompts` (nav entry "Prompts") listing kinds and versions, showing a version read-only, and offering "Edit as new version" with a textarea, an optional one-line note and Save, and MUST show that validity of the output is only checked when a run happens and that the new file must be committed.

#### Scenario: View
- **GIVEN** checklist v1 and v2
- **WHEN** the user opens `/prompts` and selects checklist v1
- **THEN** the body is shown read-only and the latest version is marked

#### Scenario: Save new version
- **GIVEN** the edit box with a changed body
- **WHEN** the user presses Save
- **THEN** the page shows the new version selected with its file path

#### Scenario: Error shown
- **GIVEN** a body without `{{transcript}}`
- **WHEN** the user presses Save
- **THEN** the server message is shown next to the button and the text stays in the box

#### Scenario: Old versions cannot be edited
- **GIVEN** any version selected
- **WHEN** the user looks for a way to overwrite it
- **THEN** only "Edit as new version" exists

### Requirement: Version picker in the Analyse form
The Analyse AI form SHALL show a version `<select>` beside each ticked output kind that has more than one version (default latest) and SHALL send the chosen versions as `prompt_versions`; the analyses list SHALL show the used version for kinds that have more than one version, and the compare pane header SHALL show the version per side.

#### Scenario: Picker appears
- **GIVEN** checklist has two versions and tags has one
- **WHEN** the form is shown
- **THEN** a version select exists for checklist only

#### Scenario: Request body
- **GIVEN** checklist v1 chosen
- **WHEN** Start analysis is pressed
- **THEN** the request carries `prompt_versions {"checklist": 1}`

#### Scenario: Compare header
- **GIVEN** a comparison of checklist v1 and v2
- **WHEN** the Checklist tab is open
- **THEN** pane A shows "checklist v1" and pane B shows "checklist v2"
