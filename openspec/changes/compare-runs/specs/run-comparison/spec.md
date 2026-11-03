## ADDED Requirements

### Requirement: Comparison storage and creation
The system SHALL store comparisons in `comparison` and `comparison_item` (one row per side A and B, each an STT run plus an optional analysis group) created by migration `0006`, and `POST /api/comparisons` MUST reject identical sides, runs from different recordings, STT runs that are not `done`, and analysis groups that do not belong to the side's STT run.

#### Scenario: Create a comparison
- **GIVEN** two done STT runs of one recording
- **WHEN** the client POSTs them as sides A and B
- **THEN** the response is 201 with an id
- **AND** two `comparison_item` rows exist with sides `A` and `B`

#### Scenario: Identical sides
- **GIVEN** one STT run
- **WHEN** it is posted as both A and B with the same analysis group
- **THEN** the response is 422

#### Scenario: Different recordings
- **GIVEN** runs from two recordings
- **WHEN** they are posted together
- **THEN** the response is 422

#### Scenario: Foreign analysis group
- **GIVEN** an analysis group made on STT run 1
- **WHEN** it is posted with STT run 2
- **THEN** the response is 422

#### Scenario: Run deleted later
- **GIVEN** an existing comparison
- **WHEN** one of its STT runs is deleted
- **THEN** its comparison items are removed
- **AND** the service deletes the comparison because it has fewer than 2 items
- **AND** `GET /api/comparisons?recording_id=` no longer returns it

### Requirement: Comparison list
The system SHALL expose `GET /api/comparisons?recording_id=` returning the recording's comparisons that have both sides, newest first.

#### Scenario: List for a recording
- **GIVEN** two comparisons on recording X and one on recording Y
- **WHEN** the client requests `?recording_id=X`
- **THEN** exactly the two comparisons of X are returned

#### Scenario: Missing filter
- **GIVEN** a request without `recording_id`
- **WHEN** it is sent
- **THEN** the response is 422

### Requirement: Comparison header
The system SHALL return for each side the run label, STT model label, STT wall time and RTF, AI model label and wall time (or null) and date, and the page SHALL show them in a sticky header per pane with a note that wall time is measured on this Mac.

#### Scenario: Header data
- **GIVEN** side A is turbo 4.2 s with Gemma E4B 12 s and side B is large-v3 9.7 s with no analysis
- **WHEN** the comparison is fetched
- **THEN** side A has both timings and side B has `ai: null`
- **AND** the page header shows "no AI step" for B

### Requirement: Transcript word diff without freezing
The system SHALL compute the word-level transcript diff in a Web Worker, anchored on words unique to both texts and split into regions of at most `maxRegionWords` with a per-region cell cap, SHALL post progress per batch, and SHALL render results to the DOM in batches of at most 500 nodes per animation frame.

#### Scenario: Small diff is exact
- **GIVEN** A `το Γκέμα και το n8n` and B `το Gemma και το n8n`
- **WHEN** the diff runs
- **THEN** the ops are `eq`, `del Γκέμα`, `ins Gemma`, `eq και το n8n`

#### Scenario: Ops rebuild both texts
- **GIVEN** any pair of texts
- **WHEN** the ops are joined with `eq` and `del` words for A and `eq` and `ins` words for B
- **THEN** both original word sequences are reproduced exactly

#### Scenario: One-hour transcript
- **GIVEN** two synthetic transcripts of about 10000 words with 5% edits
- **WHEN** the diff runs in node
- **THEN** it completes within 10 seconds
- **AND** at least 10 progress batches were posted
- **AND** both texts are rebuilt exactly

#### Scenario: No shared anchors
- **GIVEN** two texts with no common unique words and regions above the cell cap
- **WHEN** the diff runs
- **THEN** it finishes without hanging
- **AND** the result is marked coarse

#### Scenario: Change marks are not colour only
- **GIVEN** a rendered diff
- **WHEN** the user views pane A and pane B
- **THEN** deleted words are struck through with a background and inserted words are underlined with a background

### Requirement: Side-by-side outputs with counts
The system SHALL show each output kind of both sides next to each other without a text diff, with per-side counts computed by code (debrief: decisions, action items, risks, open questions; notes: sections, bullets; checklist: items, done; tags: tags; and unverified items per kind), and SHALL show "invalid output" for a kind whose run has `schema_valid=false`.

#### Scenario: Counts
- **GIVEN** side A has a checklist of 5 items with 2 done and side B has 3 items
- **WHEN** the Checklist tab is opened
- **THEN** A shows "5 items · 2 done" and B shows "3 items · 0 done"

#### Scenario: Invalid output
- **GIVEN** side B's debrief has `schema_valid=false`
- **WHEN** the Debrief tab is opened
- **THEN** B shows "invalid output" instead of counts
- **AND** A still shows its debrief

#### Scenario: Side without analysis
- **GIVEN** side B is STT only
- **WHEN** an output tab is opened
- **THEN** B shows "no outputs"

### Requirement: Different-transcripts warning
The system SHALL return a `different_transcripts` warning and the page SHALL display it above the outputs when both sides have analyses whose `input_text_sha256` differ, and MUST NOT warn when the hashes are equal or when a side has no analysis.

#### Scenario: Different input
- **GIVEN** analysis A ran on STT run 1 and analysis B on STT run 2 with different transcript text
- **WHEN** the comparison is fetched
- **THEN** `warnings` contains `different_transcripts`
- **AND** the page shows "Different transcripts — AI differences may come from speech-to-text."

#### Scenario: Same input
- **GIVEN** two analyses on the same STT run
- **WHEN** the comparison is fetched
- **THEN** `warnings` is empty

### Requirement: Script-based scoring
The system SHALL, when the recording has a script, compute WER (all and ignoring hesitations) and tech-term, number, filler and hesitation recall for each side's STT run with the same `norm()` and `score()` logic as `eval/score.py` applied to the script's stored body, SHALL store the result once per `(stt_run_id, script_sha256)` in `eval_result`, and SHALL show it in the compare view.

#### Scenario: Parity with v1 scorer
- **GIVEN** the shipped meeting script and three hypotheses
- **WHEN** `score_run` and `eval/score.py` `score()` both score them
- **THEN** every metric is identical

#### Scenario: Stored once
- **GIVEN** a comparison for a recording with a script
- **WHEN** the same comparison is created twice
- **THEN** exactly one `eval_result` row exists per STT run

#### Scenario: Shown in the page
- **GIVEN** stored `eval_result` rows for both sides
- **WHEN** the compare view loads
- **THEN** a table shows WER, WER ignoring hesitations, tech words right % and numbers right % for A and B
- **AND** missed tech terms are listed

#### Scenario: No script
- **GIVEN** a recording without a script
- **WHEN** the comparison is fetched
- **THEN** `eval` is null
- **AND** the page says "no script reference for this recording"

#### Scenario: Script without reference lines
- **GIVEN** a script body with no `**Name:** text` lines
- **WHEN** the comparison is created
- **THEN** no error occurs
- **AND** `eval` is null

### Requirement: Compare entry in the runs list
The system SHALL add a checkbox to each run row on the Analyse page, SHALL allow at most two ticked runs, SHALL enable Compare selected only at exactly two, and SHALL open the compare view at `/analyse?rec=<id>&compare=<cid>` so a reload shows the same comparison, with Swap and Close controls and stacked panes with an A | B toggle below 700 px.

#### Scenario: Third tick blocked
- **GIVEN** two runs are ticked
- **WHEN** the user looks at the other checkboxes
- **THEN** they are disabled with a visible reason line

#### Scenario: Compare button
- **GIVEN** exactly two ticked runs
- **WHEN** the user presses Compare selected
- **THEN** the compare view opens
- **AND** the URL contains `compare=<id>`

#### Scenario: Reload keeps the view
- **GIVEN** an open compare view
- **WHEN** the page is reloaded
- **THEN** the same comparison is shown

#### Scenario: Phone width
- **GIVEN** a viewport narrower than 700 px
- **WHEN** the compare view is open
- **THEN** only one pane is shown with an A | B toggle
- **AND** diff marks remain visible
