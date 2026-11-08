# speakers-diarization Specification

## Purpose
Find out who talked in a recording: run pyannote community-1 offline as its own child process, align its turns to a transcript by time overlap, show talk time and turns with click-to-seek, let the user rename speakers, and let analyses read speaker-labelled text.

## ADDED Requirements

### Requirement: Diarization storage
The system SHALL store each diarization as an append-only `diarization_run` row with `diar_turn` rows (integer millisecond times, integer speaker numbers from 1) and a mutable `speaker_name` table keyed by run and speaker, created by migration `0009`, with every foreign key to `recording` and to `diarization_run` cascading on delete, and SHALL add a nullable `analysis_run.diarization_run_id` that is set to NULL when its run is deleted.

#### Scenario: Re-running creates a new row
- **GIVEN** a recording with one done `diarization_run`
- **WHEN** the user starts another with the same settings
- **THEN** a second `diarization_run` exists
- **AND** the first run and its turns are unchanged

#### Scenario: Deleting a recording cascades
- **GIVEN** a recording with a done run, 5 turns and 1 speaker name
- **WHEN** the recording row is deleted
- **THEN** its `diarization_run`, `diar_turn` and `speaker_name` rows are gone

#### Scenario: Deleting a run keeps analyses
- **GIVEN** an `analysis_run` that references a diarization run
- **WHEN** the diarization run is deleted
- **THEN** the analysis row still exists with `diarization_run_id` NULL

#### Scenario: Status values
- **GIVEN** the `diarization_run` table
- **WHEN** a row with a status other than `queued`, `running`, `done`, `failed`, `cancelled` is inserted
- **THEN** the insert fails

#### Scenario: Invalid turn refused by the database
- **GIVEN** a diarization run
- **WHEN** a turn with `end_ms <= start_ms` or `speaker = 0` is inserted
- **THEN** the insert fails

### Requirement: Diarization settings and token handling
The system SHALL read `[diarization]` settings (`model`, `device`, `merge_gap_ms`, `stall_s`, `keepalive_s`, `progress_min_interval_s`, `plugins`, all with defaults) and SHALL read the Hugging Face token only from `DETAILS_DIARIZATION_HF_TOKEN` or `HF_TOKEN` in the environment or `.env`, MUST refuse an `hf_token` key in `config.toml` with an error naming the environment variable, and MUST NOT show the token in `repr(settings)`, API bodies, job logs, step records, error columns or exception text.

#### Scenario: Token from the environment
- **GIVEN** `HF_TOKEN` is set in the environment passed to `load_settings`
- **WHEN** settings load
- **THEN** `settings.diarization.hf_token` equals it
- **AND** `repr(settings.diarization)` does not contain it

#### Scenario: Token in config.toml is refused
- **GIVEN** a `config.toml` with `[diarization] hf_token = "x"`
- **WHEN** settings load
- **THEN** loading fails with a message that says to use `HF_TOKEN` or `.env`
- **AND** the message does not contain the value

#### Scenario: Defaults without a section
- **GIVEN** a `config.toml` with no `[diarization]` section
- **WHEN** settings load
- **THEN** `device` is `cpu` and `merge_gap_ms` is 1500

#### Scenario: Bad device
- **GIVEN** `DETAILS_DIARIZATION_DEVICE=cuda`
- **WHEN** settings load
- **THEN** loading fails naming `diarization.device` and the allowed values `cpu` and `mps`

#### Scenario: Token pattern is redacted even if unknown to settings
- **GIVEN** the text `401 for token hf_abcdefghijklmnopqrstuvwxyz0123456789`
- **WHEN** it passes through `redact()` with no configured secrets
- **THEN** the `hf_...` value is replaced by `***`

### Requirement: Token kept out of other processes
The system SHALL remove `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN` and `DETAILS_DIARIZATION_HF_TOKEN` from the server's own process environment right after settings are loaded by `python -m details` (function `scrub_secret_env`), so that no child process the server starts (STT worker, live worker, `lms`, diarization worker) inherits the token, while `settings.diarization.hf_token` keeps the value for `diar_fetch`.

#### Scenario: Scrub after load
- **GIVEN** an environment mapping holding the three variable names and `.env`-merged values
- **WHEN** `scrub_secret_env` runs after `load_settings`
- **THEN** none of the three names is in the mapping
- **AND** `settings.diarization.hf_token` still equals the value

#### Scenario: Other workers do not see it
- **GIVEN** a server started through `details.__main__.main` with a token in `.env` and the fake STT engine recording its environment
- **WHEN** an STT job runs
- **THEN** the STT worker's recorded environment has no token variable

#### Scenario: Entry point calls it
- **GIVEN** `details.__main__.main` with `web.run_app` replaced by a stub
- **WHEN** it runs
- **THEN** `scrub_secret_env` has been called before `create_app`

### Requirement: Diarization engine registry
The system SHALL resolve each `[diar.<id>]` entry in `models.toml` to an engine class through a name-keyed registry (shipped engine id `pyannote`), where an engine returns turns shaped `{start_ms, end_ms, speaker}`, and SHALL ship exactly one `diar` entry `community-1` with repo `pyannote/speaker-diarization-community-1`.

#### Scenario: Shipped entry
- **GIVEN** the repository `models.toml`
- **WHEN** the registry loads
- **THEN** `registry.diar` has `community-1` with engine `pyannote`

#### Scenario: Unknown engine id
- **GIVEN** a `[diar.x]` block with `engine = "whisperx"`
- **WHEN** the registry loads
- **THEN** loading fails with a message naming `diar.x` and the allowed engine id `pyannote`

#### Scenario: Configured model must exist
- **GIVEN** `diarization.model = "nope"`
- **WHEN** the application starts
- **THEN** startup fails with a message naming `diarization.model`

#### Scenario: Fake engine replaces pyannote in tests
- **GIVEN** `FAKE_DIAR_OVERRIDE=1` and the plugin module `fakes.diar` registering the fake under the id `pyannote`
- **WHEN** a run is started for `community-1`
- **THEN** the stored turns are the fake engine's turns
- **AND** the real `pyannote` adapter is not imported

### Requirement: Diarization availability
The system SHALL report each `diar` entry in `GET /api/models` with `installed` and, when false, a plain `unavailable_reason` and `hint`, without importing torch and without failing. Installed means `pyannote_importable()` and `model_cached(hf_hub, repo)` are both true; a cached model needs no token. The first MUST return false instead of raising when `pyannote` is absent; the second needs `config.yaml` plus a weight file in one snapshot.

#### Scenario: Package genuinely absent
- **GIVEN** an interpreter where `pyannote` is not installed (no patching of the seam)
- **WHEN** a client requests `GET /api/models?kind=diar`
- **THEN** the response is 200 with `installed:false` and a reason saying it is not installed
- **AND** the hint names `make setup-diar`

#### Scenario: Seam never raises
- **GIVEN** `importlib.util.find_spec` raising `ModuleNotFoundError` or `ValueError`
- **WHEN** `pyannote_importable()` is called
- **THEN** it returns false

#### Scenario: Model missing and no token
- **GIVEN** `pyannote_importable()` is true, the cache has no snapshot and no token is set
- **WHEN** a client requests `GET /api/models`
- **THEN** the `diar` entry has `installed:false`
- **AND** the hint mentions creating a free Hugging Face token, accepting the model terms, `HF_TOKEN` in `.env` and `make diar-model`
- **AND** the hint does not contain any token value

#### Scenario: Model missing, token set
- **GIVEN** the package is importable, no snapshot, a token is set
- **WHEN** a client requests `GET /api/models`
- **THEN** the hint says to run `make diar-model` and accept the terms first

#### Scenario: Partial download
- **GIVEN** the package is importable and a snapshot holds `config.yaml` but no weight file
- **WHEN** a client requests `GET /api/models`
- **THEN** the entry has `installed:false` and the reason says the download is incomplete
- **AND** the hint says to run `make diar-model` again

#### Scenario: Cached model, no token
- **GIVEN** the package is importable and the snapshot is complete and no token is set
- **WHEN** a client requests `GET /api/models`
- **THEN** the `diar` entry has `installed:true`

#### Scenario: Application still starts
- **GIVEN** neither the package nor the model nor a token exists
- **WHEN** the application starts and `/api/models` is requested
- **THEN** both succeed with HTTP 200

### Requirement: Diarize step and worker child
The system SHALL run diarization as the step `diarize` (installed by `install(registry)`), which awaits `ctx.memory.release_for_stt()` and then spawns `diar_worker` through `/usr/bin/env -u HF_TOKEN -u HUGGING_FACE_HUB_TOKEN -u DETAILS_DIARIZATION_HF_TOKEN HF_HUB_OFFLINE=1 HF_HUB_CACHE=<settings.paths.hf_hub>`, keeps its files in `ctx.step_dir`, reads the run id from `ctx.params`, and MUST NOT put the token in the spec, argv or worker environment.

#### Scenario: Memory released first
- **GIVEN** a fake `ctx.memory`
- **WHEN** the step starts
- **THEN** `release_for_stt()` is awaited before `spawn_child` is called

#### Scenario: Worker environment is offline and token-free
- **GIVEN** all three token variables are set in the server environment and the fake engine records its environment
- **WHEN** the step runs
- **THEN** the recorded environment has `HF_HUB_OFFLINE=1`, `HF_HUB_CACHE` equal to `settings.paths.hf_hub` and none of the three variables
- **AND** `spec.json` and the argv do not contain the token

#### Scenario: Device reaches the engine
- **GIVEN** `diarization.device = "mps"` and a run with `min_speakers=2`, `max_speakers=3`
- **WHEN** the worker calls the engine
- **THEN** the engine receives device `mps`, `min_speakers=2`, `max_speakers=3`
- **AND** `PYTORCH_ENABLE_MPS_FALLBACK=1` is set in the worker before the engine loads

#### Scenario: Run starts and clears an old error
- **GIVEN** a run that is `queued`, or `failed` with an error and re-queued by a job retry
- **WHEN** the step begins
- **THEN** the run is `running`, `started_at` is set (kept if already set) and `error` is NULL

#### Scenario: Retry after failure
- **GIVEN** a failed job whose finalizer set the run to `failed`
- **WHEN** the job is retried and finishes
- **THEN** the run ends `done` with `error` NULL
- **AND** the finalizer never overwrites a `done` run

#### Scenario: Run row missing
- **GIVEN** a step whose `diarization_run_id` has no row
- **WHEN** the step starts
- **THEN** it fails with the message "diarization run not found"

#### Scenario: Heartbeat lines
- **GIVEN** the fake engine reporting 3 progress callbacks and `progress_min_interval_s = 0`
- **WHEN** the worker runs and stdout is captured
- **THEN** there is a line at load and one `progress` line per callback
- **AND** the step turns them into `ctx.progress` calls naming the phase

#### Scenario: Throttled progress still beats
- **GIVEN** 50 callbacks within a second and the default `progress_min_interval_s`
- **WHEN** the worker runs
- **THEN** at least one `progress` line is printed and no more than the throttle allows

#### Scenario: Keepalive covers a slow model load
- **GIVEN** a fake engine whose `load` takes longer than the runner heartbeat timeout and `keepalive_s` shorter than that timeout
- **WHEN** the step runs
- **THEN** `alive` lines are printed during the load
- **AND** the run finishes `done`

#### Scenario: Keepalive stops on a stall
- **GIVEN** a fake engine that hangs after loading and `stall_s` shorter than the runner heartbeat timeout
- **WHEN** it runs
- **THEN** the worker stops printing after `stall_s`
- **AND** the runner kills it and the run is `failed` with a heartbeat message

#### Scenario: Cancel kills the child
- **GIVEN** a running diarize job
- **WHEN** the job is cancelled
- **THEN** the child process is no longer alive
- **AND** the run is `cancelled`

#### Scenario: Resume without re-running the engine
- **GIVEN** a step dir that already holds `turns.json` and `worker.done.json`
- **WHEN** the step is retried
- **THEN** no child is spawned and the turns are ingested once

#### Scenario: Worker message replaces the exit code
- **GIVEN** a worker that fails because the model cannot load and wrote `error.json`
- **WHEN** `spawn_child` raises `StepError("child exited with code 3")`
- **THEN** the step re-raises a `StepError` with the `error.json` message
- **AND** `diarization_run.error` is that message and the run has no `diar_turn` rows

#### Scenario: No error file
- **GIVEN** a worker killed without writing `error.json`
- **WHEN** the step fails
- **THEN** the run's error is the runner's own message

#### Scenario: Token only in worker stderr
- **GIVEN** a worker that writes a token-like `hf_...` string only to its stderr and exits non-zero
- **WHEN** the step fails
- **THEN** the string is absent from `child.stderr.log` as served, the job log tail, `step.json` and `diarization_run.error`

#### Scenario: Live preview waits for diarization
- **GIVEN** a diarize step holding the GPU lease and a `live` request
- **WHEN** the diarize step ends
- **THEN** the `live` request is granted after it and before any other waiting job
- **AND** the running step was not interrupted

### Requirement: Turn ingest and talk-time numbers by code
The system SHALL store turns sorted and clipped to the recording length with overlaps clamped and empty turns dropped, SHALL renumber raw engine labels to integers 1..n by first appearance, and SHALL compute `diarization_run.speakers` (`speaker`, `talk_ms`, `share_pct` to one decimal as a share of total talk time, `turns` after merging same-speaker gaps below `merge_gap_ms`) in code, never from an LLM.

#### Scenario: Numbers
- **GIVEN** engine turns SPEAKER_01 0-6000 ms and 9000-10000 ms, SPEAKER_00 6000-9000 ms
- **WHEN** the run is stored
- **THEN** SPEAKER_01 is speaker 1 with `talk_ms` 7000, `share_pct` 70.0, `turns` 2
- **AND** SPEAKER_00 is speaker 2 with `talk_ms` 3000, `share_pct` 30.0, `turns` 1

#### Scenario: Rounding
- **GIVEN** three speakers with exactly one third of the talk time each
- **WHEN** the run is stored
- **THEN** each `share_pct` is 33.3 and the sum is allowed to be 99.9

#### Scenario: Close gaps merge in the turn count
- **GIVEN** `merge_gap_ms` 1500 and speaker 1 turns 0-1000 and 1800-3000
- **WHEN** the run is stored
- **THEN** speaker 1 has `turns` 1 and `talk_ms` 2200

#### Scenario: Overlap clamped and clipped
- **GIVEN** turns that overlap by 200 ms and one that ends 500 ms after the recording ends
- **WHEN** the run is stored
- **THEN** no stored turns overlap and no turn ends after `audio_ms`

#### Scenario: No speech
- **GIVEN** an engine that returns no turns
- **WHEN** the run is stored
- **THEN** the status is `done` and `speakers` is an empty list

### Requirement: Alignment by time overlap
The system SHALL assign each transcript segment the speaker with the largest overlap in milliseconds, break ties by the lower speaker number, assign none (shown as "Unknown") when there is no overlap, and MUST NOT write the result into `transcript_segment`.

#### Scenario: Largest overlap wins
- **GIVEN** a segment 0-10000 ms, speaker 1 turn 0-3000 and speaker 2 turn 3000-10000
- **WHEN** it is aligned
- **THEN** the segment's speaker is 2

#### Scenario: Tie
- **GIVEN** a segment 0-2000 ms with speaker 2 on 0-1000 and speaker 1 on 1000-2000
- **WHEN** it is aligned
- **THEN** the speaker is 1

#### Scenario: Unknown, never guessed
- **GIVEN** a segment 50000-52000 ms and no turn near it
- **WHEN** it is aligned
- **THEN** the speaker is null and the display name is "Unknown"

#### Scenario: One speaker per segment
- **GIVEN** a segment 0-10000 ms where speaker 1 talks 0-4000 and speaker 2 talks 4000-10000
- **WHEN** it is aligned
- **THEN** the whole segment is speaker 2, while the talk-time numbers still count 4000 ms for speaker 1

#### Scenario: Stored segments unchanged
- **GIVEN** a done STT run
- **WHEN** segments are requested with a diarization run
- **THEN** the `transcript_segment` rows in the database are identical before and after

### Requirement: Diarization run API
The system SHALL expose `POST` and `GET /api/recordings/{id}/diarization-runs`, `GET /api/diarization-runs/{id}`, `GET /api/diarization-runs/{id}/turns` and `DELETE /api/diarization-runs/{id}`. POST validates the model and optional whole-number `min_speakers` and `max_speakers` (1-10, min not above max), inserts a `queued` row, enqueues a `diarize` job and returns 202 with `diarization_run_id` and `job_id`; the list also carries each run's `job_id`.

#### Scenario: Start a run
- **GIVEN** a recording and an installed diarization model
- **WHEN** the client POSTs `{min_speakers:2, max_speakers:3}`
- **THEN** the response is 202 with both ids
- **AND** the run is listed with status `queued` and its hints in `settings`

#### Scenario: Unavailable model refused
- **GIVEN** the diarization model is not installed
- **WHEN** the client POSTs
- **THEN** the response is 409 with the reason and the hint
- **AND** no row and no job is created

#### Scenario: Bad model id
- **GIVEN** a `model_id` that is unknown, or is an STT or LLM id
- **WHEN** the client POSTs
- **THEN** the response is 422 and nothing is created

#### Scenario: Bad speaker hints
- **GIVEN** hints `min_speakers:4, max_speakers:2`, or `0`, or `11`, or `"two"`
- **WHEN** the client POSTs
- **THEN** the response is 422 and nothing is created

#### Scenario: Unknown or trashed recording
- **GIVEN** an unknown id or a trashed recording
- **WHEN** the client POSTs
- **THEN** the response is 404

#### Scenario: Run detail with names
- **GIVEN** a done run where speaker 2 is named "Νίκος"
- **WHEN** the client requests `GET /api/diarization-runs/{id}`
- **THEN** `speakers` lists `speaker`, `name`, `display`, `talk_ms`, `share_pct`, `turns`
- **AND** speaker 1 has display "Speaker 1" and speaker 2 has display "Νίκος"

#### Scenario: Delete a queued or running run
- **GIVEN** a run with status `queued` or `running`
- **WHEN** the client sends DELETE
- **THEN** the response is 409 and the run still exists

#### Scenario: Job id in the list
- **GIVEN** a queued run
- **WHEN** the runs list is requested
- **THEN** the run has `job_id` equal to the job it enqueued

#### Scenario: Run label
- **GIVEN** the third done run of a recording, model label "pyannote community-1", wall 192 s, 3 speakers
- **WHEN** the runs list is requested
- **THEN** its label contains `#3`, the model label, `3:12` and `3 speakers`

### Requirement: Terminal finalizer for diarization
The system SHALL register `@steps.on_terminal("diarize")` so that a job that ends cancelled or failed, including one still queued, sets its `diarization_run` to `cancelled` or `failed` with the error text, redacted.

#### Scenario: Cancelled while queued
- **GIVEN** a queued diarize job
- **WHEN** it is cancelled before it starts
- **THEN** the run is `cancelled`

#### Scenario: Failed job
- **GIVEN** a running step that raises
- **WHEN** the job ends `failed`
- **THEN** the run is `failed` with the error text
- **AND** a token-like value in the error text is masked

### Requirement: Speaker rename
The system SHALL let the user set or reset a speaker's name with `PATCH /api/diarization-runs/{id}/speakers/{n}` (`{name}` or `{name:null}`), where the name is trimmed and Unicode-NFC normalised, 1-60 characters, without control characters, line breaks or a colon, unique within the run after NFC and case-folding against the other speakers' display names, and MUST NOT modify transcripts, `stt_run.text` or turns.

#### Scenario: Rename and reset
- **GIVEN** a done run with speakers 1 and 2
- **WHEN** the client sets speaker 1 to "Ελένη" and later sends `{name:null}`
- **THEN** the display is "Ελένη" after the first call and "Speaker 1" after the second

#### Scenario: Transcripts untouched
- **GIVEN** a done STT run and a diarization run
- **WHEN** a speaker is renamed
- **THEN** the hash of all `transcript_segment` text and `stt_run.text` is the same before and after

#### Scenario: Duplicate name
- **GIVEN** speaker 2 is named "Νίκος"
- **WHEN** the client sets speaker 1 to "νίκος", to "ΝΊΚΟΣ" in a different Unicode form, or to "Speaker 2"
- **THEN** the response is 409 and speaker 1 is unchanged

#### Scenario: Invalid names
- **GIVEN** a done run
- **WHEN** the name is empty after trimming, 61 characters long, or contains a line break or a colon
- **THEN** the response is 422

#### Scenario: Unknown speaker
- **GIVEN** a run with 2 speakers
- **WHEN** the client renames speaker 3
- **THEN** the response is 404

#### Scenario: Cross-origin rename refused
- **GIVEN** a done run
- **WHEN** a PATCH arrives with `Origin: http://evil.example`
- **THEN** the response is 403 and the name is unchanged

### Requirement: Segments with speakers
The system SHALL add `speaker` (integer or null) and `speaker_name` (display string, "Unknown" when null) to each row of `GET /api/stt-runs/{id}/segments?diarization_run_id=<id>`, SHALL answer 422 when that run is not done or belongs to another recording, and SHALL return exactly the current response when the parameter is absent.

#### Scenario: Labels present
- **GIVEN** a done STT run with 3 segments and a done diarization run
- **WHEN** segments are requested with the parameter
- **THEN** every row has `speaker` and `speaker_name` computed by overlap

#### Scenario: Parameter absent
- **GIVEN** the same runs
- **WHEN** segments are requested without the parameter
- **THEN** the rows have exactly the keys they have today

#### Scenario: Other recording
- **GIVEN** a diarization run of another recording
- **WHEN** it is used as the parameter
- **THEN** the response is 422

### Requirement: Speaker-labelled analyses
The system SHALL accept an optional `diarization_run_id` on `POST /api/stt-runs/{id}/analyses`, store it on every `analysis_run` of the group, and make the `analyse` step (id from `ctx.params`) pass `Name: text` lines as the segments, with one fixed instruction sentence added before the transcript of every prompt, chunks included, through a `preface` argument of `generate_kind`. Prompt files and the quote check are unchanged.

#### Scenario: Labelled prompt
- **GIVEN** a done STT run, a done diarization run with speaker 2 named "Νίκος", and a fake LM Studio
- **WHEN** an analysis is started with the `diarization_run_id`
- **THEN** the stored `rendered_prompt` contains lines beginning `Νίκος:` and `Speaker 1:`
- **AND** it contains the instruction sentence about speaker labels in quotes
- **AND** `input_text_sha256` differs from the same analysis without labels

#### Scenario: Without the parameter nothing changes
- **GIVEN** the same runs
- **WHEN** an analysis is started without `diarization_run_id`
- **THEN** the rendered prompt equals what it was before this change

#### Scenario: Quote inside a line still verifies
- **GIVEN** a labelled transcript line `Νίκος: Θα κάνουμε deploy στις 12`
- **WHEN** an item quotes `Θα κάνουμε deploy στις 12`
- **THEN** the item is `verified:true`

#### Scenario: Large transcript keeps the legend in every chunk
- **GIVEN** a labelled transcript large enough to be split into several chunks
- **WHEN** the analysis runs
- **THEN** the instruction sentence appears at the start of the transcript block of every chunk prompt
- **AND** the plain analysis of the same transcript has no such sentence in any chunk

#### Scenario: Retry keeps the labels
- **GIVEN** an analysis whose first reply was invalid so a retry row is created
- **WHEN** the retry row is inserted
- **THEN** it has the same `diarization_run_id` as the first row

#### Scenario: Refused combinations
- **GIVEN** a diarization run of another recording, or one that is not done, or an STT run with no segments (v1 import)
- **WHEN** an analysis is requested with it
- **THEN** the response is 422 and no job is enqueued

#### Scenario: Run deleted before the step
- **GIVEN** a queued analysis whose diarization run is then deleted
- **WHEN** the `analyse` step starts
- **THEN** it fails with a message that the speaker labels are gone
- **AND** no unlabelled output is stored

### Requirement: Speakers card on the Analyse page
The system SHALL show a "Who talked" card on `/analyse?rec=<id>` that starts a run with optional speaker-count hints, lists runs, and shows talk time, a timeline, turns and rename inputs for the picked run, setting all text through `textContent` with no model id in page code.

#### Scenario: Disabled with the hint
- **GIVEN** `/api/models?kind=diar` returns `installed:false` with a hint
- **WHEN** the page loads
- **THEN** the start button is disabled
- **AND** the reason and the hint are visible

#### Scenario: No bars when there is nothing to show
- **GIVEN** diarization is unavailable, or a done run has no speakers
- **WHEN** the card renders
- **THEN** the card is still shown with no bars
- **AND** it says the model is unavailable, or that no speech was found

#### Scenario: Bars use text, not colour alone
- **GIVEN** a done run with speakers at 70.0% and 30.0%
- **WHEN** the card renders
- **THEN** each bar row shows the speaker name, the percentage and the talk time as text

#### Scenario: Click seeks
- **GIVEN** a seeded recording at least 66 s long and a turns row for a segment starting at 65000 ms
- **WHEN** the user clicks it, or focuses it and presses Enter
- **THEN** `audio.currentTime` is 65 and the audio is playing

#### Scenario: Rename survives reload
- **GIVEN** a done run
- **WHEN** the user renames speaker 1 and reloads the page
- **THEN** the new name is shown in the bars, the turns list and the transcript chips

#### Scenario: Warnings always visible
- **GIVEN** a done run is shown
- **WHEN** the card renders
- **THEN** the text "Speaker labels are a guess. Check before sharing." is visible
- **AND** a second line says each transcript line gets one speaker, so talk-time percentages and the labelled text can differ

#### Scenario: Progress survives reload
- **GIVEN** a diarize job is running
- **WHEN** the page is reloaded
- **THEN** the Running card shows its stage and percent, found through the `job_id` of the run in the list

#### Scenario: GPU hint near the button
- **GIVEN** the card is shown
- **WHEN** the user looks at the start button
- **THEN** a line says that live assist on the Record page waits while speakers are being detected

### Requirement: Setup commands
The system SHALL provide `make setup-diar` (install `requirements-diar.txt` into the venv) and `make diar-model` (run `python -m details.diar_fetch`, which reads the token from settings, downloads the model snapshot into `settings.paths.hf_hub` (`cache_dir`), and on failure prints a plain message passed through `redact()` and exits non-zero), and MUST NOT require either for the default `make setup`, `make run` or `make test`.

#### Scenario: Fetch without a token
- **GIVEN** no token is set
- **WHEN** `diar_fetch` runs
- **THEN** it exits non-zero with a message that says to put `HF_TOKEN` in `.env`
- **AND** it makes no network call

#### Scenario: Downloader uses the configured cache
- **GIVEN** `settings.paths.hf_hub` is a temp folder and a fake downloader
- **WHEN** `diar_fetch` runs with a token
- **THEN** the downloader receives `cache_dir` equal to that folder, the configured repo and the token

#### Scenario: Gated repo not accepted
- **GIVEN** a fake downloader that raises a gated-repo error containing the token
- **WHEN** `diar_fetch` runs
- **THEN** the message tells the user to accept the model terms on the model page
- **AND** the token value is not printed

#### Scenario: Default targets unaffected
- **GIVEN** `pyannote.audio` is not installed
- **WHEN** `make test` runs
- **THEN** it passes without importing torch or pyannote
