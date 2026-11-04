# analysis-outputs Specification

## Purpose
Turn a finished transcript into validated, quote-checked AI outputs (debrief, notes, checklist, tags) with a local LM Studio model, and show, tick and export them on the Analyse page.

## Requirements

### Requirement: Prompt templates as git files
The system SHALL load prompt templates from `prompts/<kind>/v<N>.md` with front matter and the placeholders `{{transcript}}`, `{{language}}` and `{{recording_title}}`, SHALL sync them into `prompt_template` at startup, and MUST fail with an "edit = new version file" error when an existing `(name, version)` has a different sha256.

#### Scenario: New file is inserted
- **GIVEN** `prompts/debrief/v1.md` and an empty `prompt_template` table
- **WHEN** the sync runs
- **THEN** one row `(debrief, 1)` exists with the file's sha256

#### Scenario: Edited version is refused
- **GIVEN** `(debrief, 1)` in the table
- **WHEN** the body of `v1.md` is changed and the sync runs
- **THEN** startup fails naming `debrief` v1 and telling the user to add `v2.md`
- **AND** the stored row is unchanged

#### Scenario: Unknown placeholder
- **GIVEN** a template containing `{{speakers}}`
- **WHEN** the sync runs
- **THEN** it fails naming the placeholder

### Requirement: JSON Schemas per kind
The system SHALL ship `schemas/<kind>.v1.json` for `debrief`, `notes`, `checklist` and `tags`, with `additionalProperties:false`, `schema_version` 1, a `quote` on every item and `due` limited to `YYYY-MM-DD` or null.

#### Scenario: Valid fixture
- **GIVEN** a good checklist fixture with `due: "2026-11-15"` and `owner: null`
- **WHEN** it is validated
- **THEN** it passes

#### Scenario: Bad fixtures
- **GIVEN** a checklist item with `due: "next week"`, or an item without `quote`, or an extra field
- **WHEN** each is validated
- **THEN** each fails

#### Scenario: Only v2.0 kinds
- **GIVEN** a request for kind `diagram` or `speakers`
- **WHEN** the analysis API receives it
- **THEN** it answers 422

### Requirement: LM Studio structured-output client
The system SHALL call LM Studio `/v1/chat/completions` at `settings.lmstudio.url` once per kind with `response_format` of type `json_schema`, `reasoning_effort` `none` and a per-kind `max_tokens`, SHALL read only `message.content`, and SHALL return text, token counts, finish reason, wall time and the raw response.

#### Scenario: Request shape
- **GIVEN** a fake LM Studio server recording requests
- **WHEN** the client runs kind `tags`
- **THEN** the request has `response_format.type = "json_schema"` with the tags schema and `reasoning_effort = "none"`
- **AND** the URL came from `settings.lmstudio.url`, not code

#### Scenario: Reasoning content ignored
- **GIVEN** a reply with `reasoning_content` holding non-JSON text and `content` holding valid JSON
- **WHEN** the client parses it
- **THEN** the content JSON is used

#### Scenario: Server unreachable
- **GIVEN** no server at the configured URL
- **WHEN** an analysis runs
- **THEN** the run is `failed` with an error naming that URL
- **AND** no output rows exist

### Requirement: Validate, one retry, never repair
The system SHALL validate each model reply against its schema, make exactly one retry that includes the validation error, store the retry as a separate `analysis_run` linked by `retry_of`, and when the retry is still invalid SHALL set `schema_valid=false`, create no `output` row, keep the raw text, and MUST NOT modify the model's JSON.

#### Scenario: Retry succeeds
- **GIVEN** a first reply that fails validation and a second that passes
- **WHEN** the kind is processed
- **THEN** two `analysis_run` rows exist, the second with `retry_of` set to the first
- **AND** one `output` row exists from the second

#### Scenario: Two failures
- **GIVEN** two invalid replies
- **WHEN** the kind is processed
- **THEN** the last run has `schema_valid=false` and the raw text in `raw_response`
- **AND** there is no `output` row for that kind
- **AND** the API returns the raw text for display

### Requirement: Verbatim quote verification
The system SHALL check that each item's `quote` is a substring of the full transcript after normalisation with the same `norm()` as `eval/score.py`, SHALL store `verified` true or false on the item, and MUST keep unverified items, marking them "unverified" in the UI.

#### Scenario: Verbatim quote
- **GIVEN** a transcript containing `Θα κάνουμε deploy στις 12`
- **WHEN** an item quotes `θα κανουμε deploy στισ 12` in different case and accents
- **THEN** the item is `verified:true`

#### Scenario: Invented quote
- **GIVEN** an item whose quote does not occur in the transcript
- **WHEN** verification runs
- **THEN** the item is stored with `verified:false`
- **AND** the page shows it greyed with the text "unverified"

### Requirement: Map-reduce for long transcripts
The system SHALL process transcripts estimated above `single_pass_max_tokens` as chunks of about `chunk_tokens` split at segment boundaries, SHALL cache each chunk result on disk keyed by kind, template sha, model, params and chunk text, SHALL merge lists in code with de-duplication, and SHALL verify quotes against the full transcript.

#### Scenario: Short transcript is single pass
- **GIVEN** a transcript below the threshold
- **WHEN** an analysis runs
- **THEN** exactly one model call is made per kind

#### Scenario: Long transcript is chunked
- **GIVEN** a transcript estimated at 3 chunks
- **WHEN** a kind runs
- **THEN** 3 map calls are made, each on whole segments
- **AND** duplicate action items across chunks appear once

#### Scenario: Resume uses the cache
- **GIVEN** a job that failed after 2 of 3 chunks
- **WHEN** it is retried
- **THEN** only the third chunk is sent to the model

### Requirement: Output language
The system SHALL write outputs in the transcript's language by default and in English when `output_language` is `en`, detecting Greek by letter share when the run language is `auto`.

#### Scenario: Greek by default
- **GIVEN** an `el` transcript and no override
- **WHEN** the prompt is rendered
- **THEN** `{{language}}` is `Greek`

#### Scenario: English switch
- **GIVEN** an `el` transcript and `output_language="en"`
- **WHEN** the prompt is rendered
- **THEN** `{{language}}` is `English`

### Requirement: Analysis API
The system SHALL expose `POST /api/stt-runs/{id}/analyses`, `GET /api/stt-runs/{id}/analyses` and `GET /api/analyses/{group_id}`, SHALL group the per-kind runs of one request under one `group_id`, and SHALL record model id, params, rendered prompt, timings and token counts per run.

#### Scenario: Start an analysis
- **GIVEN** a done STT run and an available LLM model
- **WHEN** the client POSTs `{model_id, kinds:["debrief","checklist"], output_language:"transcript"}`
- **THEN** the response is 202 with `group_id` and `job_id`
- **AND** after the job finishes, `GET /api/analyses/{group_id}` lists both outputs with timings

#### Scenario: Transcript not ready
- **GIVEN** an STT run with status `running`
- **WHEN** the client POSTs an analysis for it
- **THEN** the response is 422
- **AND** no job is enqueued

#### Scenario: Non-LLM or unknown model
- **GIVEN** a `kind=stt` model id or an id not in the registry
- **WHEN** it is used as `model_id`
- **THEN** the response is 422

#### Scenario: Job steps
- **GIVEN** a valid request
- **WHEN** the job is enqueued
- **THEN** its steps are `lm_load` with the `model_id`, `analyse`, `lm_unload` in that order

### Requirement: Terminal finalizer
The system SHALL register `@steps.on_terminal("analyse")` so that a job ending cancelled or failed, including one still queued, sets its unfinished `analysis_run` rows to `cancelled` or `failed`.

#### Scenario: Cancelled while queued
- **GIVEN** a queued analysis job
- **WHEN** it is cancelled before starting
- **THEN** its `analysis_run` rows are `cancelled`

#### Scenario: Failed mid-way
- **GIVEN** a job that fails after the first kind finished
- **WHEN** it ends `failed`
- **THEN** the finished kind keeps its output
- **AND** the unfinished kinds are `failed`

### Requirement: Tickable checklist persistence
The system SHALL persist checklist ticks with `PATCH /api/outputs/{id}/items/{idx}` by atomically updating `content.items[idx].done` in the stored jsonb, and SHALL reject other kinds, bad indexes and non-boolean values.

#### Scenario: Tick survives reload
- **GIVEN** a checklist output with 3 items, all not done
- **WHEN** the client PATCHes item 1 with `{"done":true}` and then GETs the analysis
- **THEN** item 1 is `done:true` and the others are unchanged

#### Scenario: Bad index
- **GIVEN** a checklist with 3 items
- **WHEN** the client PATCHes item 7
- **THEN** the response is 404
- **AND** the content is unchanged

#### Scenario: Wrong kind
- **GIVEN** a `tags` output
- **WHEN** the client PATCHes an item
- **THEN** the response is 409

### Requirement: Markdown rendered from JSON
The system SHALL render Markdown only from the validated JSON by deterministic code, with GitHub-style `- [ ]` and `- [x]` for checklist state at render time, and SHALL serve it as `text/markdown` for copy and as an attachment for download.

#### Scenario: Golden output
- **GIVEN** a stored checklist with one done and one open item
- **WHEN** its Markdown is requested
- **THEN** it equals the golden file with `- [x]` and `- [ ]` lines
- **AND** a second request returns identical bytes

#### Scenario: Download header
- **GIVEN** an analysis group
- **WHEN** `GET /api/analyses/{group_id}/markdown?download=1` is requested
- **THEN** the response has a `Content-Disposition` attachment filename ending in `.md`

### Requirement: Analyse page write-up UI
The system SHALL add to the Analyse page an AI model dropdown filled from `/api/models?kind=llm` showing each entry's `role` (cheap or nice) and `needs_gb`, disabling entries with `installed:false`, with no preselected model, output chips for debrief, notes, checklist and tags, tabs rendering each stored output with a footer of model, time and tokens, a tickable checklist, and Copy Markdown and Download .md controls.

#### Scenario: No forced default
- **GIVEN** the registry offers a cheap and a nice model
- **WHEN** the page loads
- **THEN** the dropdown shows a "Choose AI model" placeholder
- **AND** Start analysis is disabled until one is picked

#### Scenario: Ticking a box
- **GIVEN** a rendered checklist
- **WHEN** the user presses Space on an item's checkbox
- **THEN** a PATCH is sent
- **AND** on failure the box reverts and an error line is shown

#### Scenario: Copy Markdown
- **GIVEN** the Checklist tab is open
- **WHEN** the user presses Copy Markdown
- **THEN** the clipboard holds the text from `GET /api/outputs/{id}/markdown`

#### Scenario: Invalid output shown, not hidden
- **GIVEN** a kind with `schema_valid=false`
- **WHEN** its tab is opened
- **THEN** the raw model text and a red note "model returned invalid JSON" are shown
