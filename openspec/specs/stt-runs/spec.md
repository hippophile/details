# stt-runs Specification

## Purpose
TBD - created by archiving change stt-runs. Update Purpose after archive.

## Requirements

### Requirement: STT run storage
The system SHALL store every speech-to-text run as an append-only `stt_run` row with its `transcript_segment` rows (integer millisecond timestamps), created by migration `0004`, and SHALL never overwrite or merge runs.

#### Scenario: Re-running creates a new row
- **GIVEN** a recording with one finished `stt_run` using `large-v3`
- **WHEN** the user starts a run with the same model and settings again
- **THEN** a second `stt_run` row exists
- **AND** the first row and its segments are unchanged

#### Scenario: Deleting a recording cascades
- **GIVEN** a recording with a finished run and 10 segments
- **WHEN** the recording row is deleted
- **THEN** its `stt_run` and `transcript_segment` rows are gone

#### Scenario: Failed run leaves no segments
- **GIVEN** a run whose worker process is killed mid-way
- **WHEN** the step reports failure
- **THEN** `stt_run.status` is `failed` with a non-empty `error`
- **AND** zero `transcript_segment` rows exist for it

### Requirement: Engine registry
The system SHALL resolve each `kind=stt` entry in `models.toml` to an engine class through a name-keyed registry, where an engine returns segments shaped `{start_ms, end_ms, text, words?, avg_logprob?, no_speech_prob?}`, so adding an engine needs one class, one registry line and one config block and no change to the runner, API or page.

#### Scenario: Registered fake engine is used
- **GIVEN** a `models.toml` entry with `engine = "fake"` and a registered fake engine
- **WHEN** a run is started for that model
- **THEN** the transcript contains the fake engine's segments

#### Scenario: Unknown model id is rejected
- **GIVEN** a model id that is not in the registry
- **WHEN** a run is requested for it
- **THEN** the API answers 422
- **AND** no `stt_run` row is created and no job is enqueued

### Requirement: mlx-whisper child-process adapter
The system SHALL run mlx-whisper in a child process that transcribes the audio in windows, appends one JSON line per finished window to a checkpoint file with flush and fsync, and on resume skips windows already in that file, using `condition_on_previous_text=False` and the run's `language` and vocab-hint settings.

#### Scenario: Resume after kill
- **GIVEN** a worker killed after 3 of 5 windows
- **WHEN** the run is retried with the same `stt_run_id`
- **THEN** only windows 4 and 5 are transcribed
- **AND** the final transcript contains all 5 windows in order

#### Scenario: Settings reach the engine
- **GIVEN** a run with `language=el` and the vocab hint enabled
- **WHEN** the worker calls the engine
- **THEN** the engine receives `language="el"`, the configured `initial_prompt` and `condition_on_previous_text=False`

#### Scenario: Cancel kills the child
- **GIVEN** a running STT job
- **WHEN** the job is cancelled
- **THEN** the child process is no longer alive
- **AND** the run is `cancelled`

#### Scenario: Checkpoint lives in the step dir
- **GIVEN** a running `stt_transcribe` step
- **WHEN** a window finishes
- **THEN** a line is appended to `segments.jsonl` inside `ctx.step_dir`
- **AND** no checkpoint file is written elsewhere

#### Scenario: Memory released first
- **GIVEN** a fake `ctx.memory`
- **WHEN** the step starts
- **THEN** `release_for_stt()` is awaited before `spawn_child` is called

### Requirement: Worker heartbeat
The worker child process MUST print one stdout line when its model is loaded and at least one line per finished window.

#### Scenario: Heartbeat lines
- **GIVEN** the fake engine and a 3-window file
- **WHEN** the worker runs and its stdout is captured
- **THEN** there is a line at load
- **AND** at least 3 further lines, one per window

### Requirement: Terminal finalizer
The system SHALL register `@steps.on_terminal("stt_transcribe")` so that a job that ends cancelled or failed, including one still queued, sets its `stt_run` to `cancelled` or `failed`.

#### Scenario: Cancelled while queued
- **GIVEN** a queued job whose `stt_run` is `queued`
- **WHEN** the job is cancelled before it starts
- **THEN** the `stt_run` is `cancelled`

#### Scenario: Failed job
- **GIVEN** a running step that raises
- **WHEN** the job ends `failed`
- **THEN** the `stt_run` is `failed` with the error text

### Requirement: v1 transcript adoption
The system SHALL create one `stt_run` with `engine='mlx_whisper'`, `model_id='unknown (v1 import)'`, `status='done'`, the file's text and no segments for each recording that has an importer-written `v1-transcript.txt` and no such run, idempotently.

#### Scenario: Adopt once
- **GIVEN** a recording folder containing `v1-transcript.txt`
- **WHEN** adoption runs twice
- **THEN** exactly one `stt_run` exists with `model_id='unknown (v1 import)'` and zero segments

#### Scenario: No file, no run
- **GIVEN** a recording without `v1-transcript.txt`
- **WHEN** adoption runs
- **THEN** no `stt_run` is created

### Requirement: Library runs count
The system SHALL include `runs_count`, the number of `stt_run` rows, for each recording in the library list query.

#### Scenario: Count shown
- **GIVEN** a recording with 2 runs and another with none
- **WHEN** the library list is requested
- **THEN** `runs_count` is 2 and 0

### Requirement: Greek question-mark postfix
The system SHALL replace a `?` that directly follows a Greek letter with `;` in stored transcript text for Greek segments, and SHALL keep the untouched engine output in `stt_run.raw`.

#### Scenario: Greek question
- **GIVEN** an engine segment `Τι κάνεις?`
- **WHEN** the run is stored
- **THEN** the segment text is `Τι κάνεις;`
- **AND** `raw` still contains `Τι κάνεις?`

#### Scenario: English question is untouched
- **GIVEN** an engine segment `Is it ready?`
- **WHEN** the run is stored
- **THEN** the segment text is `Is it ready?`

### Requirement: STT run API
The system SHALL expose `POST /api/recordings/{id}/stt-runs`, `GET /api/recordings/{id}/stt-runs`, `GET /api/stt-runs/{id}`, `GET /api/stt-runs/{id}/segments` and `DELETE /api/stt-runs/{id}`, where POST validates the model, inserts a `queued` row, enqueues the job and returns 202 with `stt_run_id` and `job_id`.

#### Scenario: Start a run
- **GIVEN** a recording and an available STT model
- **WHEN** the client POSTs `{model_id, language:"el", vocab_hint:true}`
- **THEN** the response is 202 with `stt_run_id` and `job_id`
- **AND** the run appears in the list with status `queued`

#### Scenario: LLM model refused
- **GIVEN** a registry entry with `kind="llm"`
- **WHEN** the client POSTs it as `model_id`
- **THEN** the response is 422
- **AND** no job is enqueued

#### Scenario: Unavailable model refused
- **GIVEN** a model whose `installed` is false
- **WHEN** the client POSTs it
- **THEN** the response is 409 with the reason

#### Scenario: Delete a running or queued run
- **GIVEN** a run with status `running` or `queued`
- **WHEN** the client sends DELETE
- **THEN** the response is 409
- **AND** the run still exists

### Requirement: Run timing and labels
The system SHALL record `wall_ms`, `audio_ms` and `rtf` (audio seconds divided by wall seconds) on completion and SHALL return a display label per run containing the model label, wall time and RTF.

#### Scenario: Label content
- **GIVEN** a run of a 77 s recording that took 9.7 s with model label `Whisper large-v3`
- **WHEN** the runs list is requested
- **THEN** the run has `wall_ms` 9700, `rtf` about 7.9 and a label containing `Whisper large-v3`, `9.7 s` and `7.9×`

### Requirement: Analyse page shell
The system SHALL serve `web/analyse/index.html` at `/analyse?rec=<id>` containing the recording header with an audio player, a New run form whose STT model dropdown is filled from `/api/models?kind=stt`, a progress view fed by SSE with polling fallback, a runs list and a transcript view, with no hardcoded model ids in the page.

#### Scenario: No recording selected
- **GIVEN** the page is opened without `rec`
- **WHEN** it loads
- **THEN** it shows "Pick a recording in Library" with a link to `/library`
- **AND** no run form is shown

#### Scenario: Dropdown from the API
- **GIVEN** `/api/models?kind=stt` returns two models
- **WHEN** the page loads
- **THEN** the dropdown has exactly those two options
- **AND** an option with `installed:false` is disabled with its `unavailable_reason` visible

#### Scenario: Progress survives reload
- **GIVEN** a job is running for the recording
- **WHEN** the page is reloaded
- **THEN** the progress view shows the running job's stage and percent

#### Scenario: SSE drops
- **GIVEN** the SSE connection fails
- **WHEN** 2 seconds pass
- **THEN** the page polls `GET /api/jobs/{id}` and keeps updating

### Requirement: Transcript view with click-to-seek
The system SHALL render a run's segments as `[mm:ss] text` rows and SHALL set the player's current time to the segment's `start_ms` and play when a row is clicked.

#### Scenario: Click a row
- **GIVEN** a transcript with a segment starting at 65000 ms
- **WHEN** the user clicks that row
- **THEN** `audio.currentTime` is 65
- **AND** the audio is playing

#### Scenario: Row is keyboard reachable
- **GIVEN** the transcript is rendered
- **WHEN** the user tabs to a row and presses Enter
- **THEN** the player seeks to that row's time
