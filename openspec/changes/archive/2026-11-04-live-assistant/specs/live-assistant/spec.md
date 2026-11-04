# live-assistant Specification

## ADDED Requirements

### Requirement: Live assist toggle
The Record page SHALL offer an unchecked-by-default "Live assist" toggle that is sent with the WebSocket `start` message as `live: true` together with the current `silence_db`, and when it is off the system MUST NOT spawn a worker, take the GPU lease or send any live message.

#### Scenario: Off by default
- **GIVEN** the Record page is loaded
- **WHEN** the user presses Mic on without touching the toggle
- **THEN** the `start` message has no live flag
- **AND** no worker process is spawned and no `live_*` message is received

#### Scenario: On
- **GIVEN** the toggle is checked before Mic on
- **WHEN** the take starts
- **THEN** the Live card is visible and the server answers with a `live_state` message

#### Scenario: Toggle locked while recording
- **GIVEN** a take in progress
- **WHEN** the user looks at the toggle
- **THEN** it is disabled

### Requirement: Recording is never blocked by live work
The system SHALL write every audio frame with the existing writer before offering it to live, SHALL hand it to live through a synchronous call that appends to a bounded queue (`live.max_backlog_s`) and drops the oldest live chunk when full, and MUST ensure no failure, delay or exception in live code changes the written WAV or delays a write.

#### Scenario: Blocked worker
- **GIVEN** a live worker that does not answer for 5 seconds while audio keeps arriving
- **WHEN** the take is stopped
- **THEN** the saved audio is byte-identical in content to the audio sent
- **AND** every write completed without waiting for the worker

#### Scenario: Queue overflow drops live audio only
- **GIVEN** more audio queued than `max_backlog_s`
- **WHEN** the next chunk is offered
- **THEN** the oldest live chunk is dropped and counted in `dropped_ms`
- **AND** the WAV has all frames

#### Scenario: Offer cannot raise
- **GIVEN** the live offer is made to raise an exception
- **WHEN** audio keeps arriving
- **THEN** the writer keeps writing, live is marked failed and the page says so

#### Scenario: Offer is cheap
- **GIVEN** 100 ms chunks
- **WHEN** a chunk is offered
- **THEN** the call returns without awaiting anything, within 5 ms in the test

### Requirement: Whisper runs in a separate child process
The system SHALL run live speech-to-text in a persistent child process using the model `live.stt_model` (default `whisper-turbo`, refused if it is not an installed `fast`-role STT model), SHALL kill it when the session ends or the server shuts down, SHALL restart it once after a failure or a reply timeout (`live.stt_timeout_s`), and MUST report a second failure as an STT error without affecting the recording.

#### Scenario: Cold load
- **GIVEN** a child that takes longer than `stt_timeout_s` to print `ready` but less than `stt_load_timeout_s`
- **WHEN** the session starts
- **THEN** it is not restarted and live text starts after ready

#### Scenario: Busy worker
- **GIVEN** the worker is busy with a commit
- **WHEN** a partial is due
- **THEN** the partial is dropped and the commit is never delayed by it

#### Scenario: Child lifecycle
- **GIVEN** a live session
- **WHEN** the take stops
- **THEN** the child process has exited

#### Scenario: Child dies
- **GIVEN** the child exits unexpectedly twice
- **WHEN** the second failure happens
- **THEN** a `live_status` with part `stt` and level `error` is sent
- **AND** the recording continues and saves

#### Scenario: Large model refused
- **GIVEN** `live.stt_model` set to `whisper-large`
- **WHEN** a live session starts
- **THEN** live STT is refused with a message and the recording continues

#### Scenario: Model not installed
- **GIVEN** whisper-turbo is not installed
- **WHEN** a take starts with live on
- **THEN** the take records and the page says why live text is unavailable

### Requirement: Live segmentation
The system SHALL cut live speech with a pure segmenter that commits a line after 0.5 s of quiet or at 15 s (at the quietest 100 ms of the last 4 s), drops pure silence, emits partials about every 0.8 s of new speech when `live.partials` is true, and stamps each commit with start and end milliseconds from the writer's frame counter.

#### Scenario: Commit on pause
- **GIVEN** 3 s of speech followed by 0.8 s of silence
- **WHEN** the segmenter runs
- **THEN** one commit covers the speech

#### Scenario: Long speech cut
- **GIVEN** 20 s of continuous speech
- **WHEN** the segmenter runs
- **THEN** a commit is made before 16 s

#### Scenario: Silence only
- **GIVEN** 10 s below the silence line
- **WHEN** the segmenter runs
- **THEN** nothing is sent to the worker

#### Scenario: Gap
- **GIVEN** a dropped chunk in the middle of speech
- **WHEN** the next audio arrives
- **THEN** the pending speech is committed before the gap and a new segment starts after it

#### Scenario: Offsets match the file
- **GIVEN** live dropped some chunks
- **WHEN** commits are stamped
- **THEN** each start_ms corresponds to the frame position in the saved recording

### Requirement: GPU lease priority for live mode
The system SHALL use the one shared `GpuLease` (created by run-insights) between the job runner and live mode, SHALL acquire it as owner `live` for the whole session so queued jobs stay queued, MUST NOT interrupt a running job step, and SHALL release it on stop, on grace-expiry finalize and on app cleanup (the lease is released at grace expiry, not when the WS closes while a resume is still possible).

#### Scenario: Between job steps
- **GIVEN** a job at the end of `lm_load` and a live session that takes the lease
- **WHEN** live ends
- **THEN** the job's `analyse` step runs and the job completes

#### Scenario: Jobs wait
- **GIVEN** a live session holding the lease and a queued job
- **WHEN** time passes
- **THEN** the job's step does not start until the lease is released

#### Scenario: Running step is not interrupted
- **GIVEN** a job step holding the lease
- **WHEN** a live session starts
- **THEN** the step finishes first and the page shows "waiting for the running job step"
- **AND** the recording is already being written

#### Scenario: Released on every end path
- **GIVEN** a live session
- **WHEN** it ends by stop, by grace expiry, by an unresumed close, or by app cleanup
- **THEN** the lease owner becomes none (or the next waiter)

#### Scenario: Second live session
- **GIVEN** one live session holding the lease
- **WHEN** another take starts with live on
- **THEN** it records normally and shows "another live session is active"

### Requirement: Rolling notes from Gemma E4B
The system SHALL, every `live.notes_every_min` minutes (default 2) when committed text has grown and no update is running, call the model `live.llm_model` with the previous notes and the newest live transcript (at most `live.max_transcript_tokens` estimated), validate the reply against `schemas/live-rolling.v1.json` with one retry and no repair, check every item's `quote` against the whole live transcript so far, and MUST keep unverified items visible marked as unverified.

#### Scenario: Interval
- **GIVEN** `notes_every_min` 2 and a fake clock
- **WHEN** 2 minutes pass with new text
- **THEN** exactly one model call is made

#### Scenario: No new text
- **GIVEN** no new committed line since the last update
- **WHEN** the interval elapses
- **THEN** no model call is made

#### Scenario: No overlap
- **GIVEN** an update still running when the next interval elapses
- **WHEN** the tick happens
- **THEN** it is skipped

#### Scenario: Verified and unverified
- **GIVEN** a reply with one item quoting the transcript and one with an invented quote
- **WHEN** the reply is processed
- **THEN** the first has `verified: true` and the second `verified: false` and both are sent to the page

#### Scenario: Invalid reply
- **GIVEN** two invalid replies in a row
- **WHEN** the update ends
- **THEN** the previous notes stay on screen and a warn status "update skipped" is sent

#### Scenario: Long meeting
- **GIVEN** a live transcript above the token cap
- **WHEN** an update runs
- **THEN** the prompt contains only the newest text plus the previous notes
- **AND** quotes are still checked against the full transcript

### Requirement: Only the cheap model in live mode
The system SHALL use only an LLM entry with role `cheap` for live notes and MUST NOT load any other model while a live session exists.

#### Scenario: 26B configured
- **GIVEN** `live.llm_model` is `gemma-nice`
- **WHEN** a live session starts
- **THEN** live notes are disabled with a message
- **AND** FakeLMStudio recorded no `load` call

#### Scenario: Unknown id
- **GIVEN** `live.llm_model` is not in models.toml
- **WHEN** settings and registry load
- **THEN** startup fails with a message naming `live.llm_model`

#### Scenario: Release at the end
- **GIVEN** the cheap model was loaded by live
- **WHEN** the session ends
- **THEN** the app-owned model is unloaded

### Requirement: LM Studio problems do not hurt the recording
The system SHALL show a plain message naming `settings.lmstudio.url` when LM Studio cannot be reached, a model cannot be loaded for lack of memory, or a call exceeds `live.llm_timeout_s`, SHALL keep recording and live text running, and SHALL retry on the next interval.

#### Scenario: Unreachable at start
- **GIVEN** LM Studio is down
- **WHEN** the first update is due
- **THEN** a `live_status` with part `notes` and level `error` names the URL and says recording continues
- **AND** live text keeps appearing

#### Scenario: Recovery
- **GIVEN** LM Studio comes up later
- **WHEN** the next interval elapses
- **THEN** the model loads and notes appear

#### Scenario: Not enough memory
- **GIVEN** the memory preflight refuses the load
- **WHEN** the update is due
- **THEN** the message states needed and free GB and the take is unaffected

### Requirement: Live preview saved as an STT run
The system SHALL, when a take with live commits is finalized (stop, grace expiry), insert after the recording row an `stt_run` with `engine` `mlx_whisper`, `model_id` = `live.stt_model`, `status` `done`, `text` joined from the commits, one `transcript_segment` per commit, `settings.live_preview` true, `dropped_ms`, and a label ending in "(live preview)", and MUST NOT let a failure of this insert fail the finalize.

#### Scenario: Preview run exists
- **GIVEN** a live take with 3 commits that is stopped
- **WHEN** finalize completes
- **THEN** the recording exists and one done `stt_run` with 3 segments and `settings.live_preview` true
- **AND** its label contains "(live preview)"

#### Scenario: No live, no run
- **GIVEN** a take without live assist
- **WHEN** it is stopped
- **THEN** no `stt_run` is created

#### Scenario: Too short
- **GIVEN** a live take under 0.5 s
- **WHEN** it is stopped
- **THEN** nothing is saved and no run exists

#### Scenario: Stop does not wait for live
- **GIVEN** a worker blocked for 5 seconds
- **WHEN** the user stops the take
- **THEN** `saved` is sent in under 3 seconds, with the preview built from commits already received

#### Scenario: Insert fails
- **GIVEN** the preview insert raises
- **WHEN** finalize runs
- **THEN** the recording is saved, the error is logged and the saved card appears

#### Scenario: Usable downstream
- **GIVEN** a live-preview run
- **WHEN** the Analyse page lists runs
- **THEN** it is listed with its label, an analysis can be started on it, it can be ticked for compare, and the page does not auto-select it when a non-live done run exists

### Requirement: Live card on the Record page
The Record page SHALL show, while live is on, per-part status lines (speech, notes, GPU queue), the committed transcript with the partial line in grey, and Notes, Action items and Tags lists with an "unverified" marker and the time of the last update, SHALL set all text with `textContent`, and SHALL replay committed lines and the last notes after a resume.

#### Scenario: Lines appear
- **GIVEN** a running live take
- **WHEN** `live_commit` messages arrive
- **THEN** each line is appended and the partial is replaced

#### Scenario: Unverified marked
- **GIVEN** a notes update containing an unverified item
- **WHEN** it is rendered
- **THEN** the item shows "unverified"

#### Scenario: Error shown, recording goes on
- **GIVEN** a `live_status` error for notes
- **WHEN** it arrives
- **THEN** the status line shows the message and the timer keeps running

#### Scenario: Resume
- **GIVEN** the connection dropped and resumed
- **WHEN** the server answers `ready`
- **THEN** a `live_snapshot` restores the lines and the last notes

#### Scenario: Text is inert
- **GIVEN** a live line containing `<img src=x onerror=alert(1)>`
- **WHEN** it is rendered
- **THEN** it appears as text and no element is created

### Requirement: Live settings section
The system SHALL read `[live]` (`settings.live.*`, env `DETAILS_LIVE_<KEY>`) with keys `stt_model`, `llm_model`, `language`, `notes_every_min`, `partials`, `max_backlog_s`, `max_transcript_tokens`, `llm_timeout_s`, `stt_timeout_s`, `stt_load_timeout_s`, `vocab_hint`, and SHALL validate them in the section hook.

#### Scenario: Defaults
- **GIVEN** the shipped config
- **WHEN** settings load
- **THEN** `notes_every_min` is 2, `stt_model` is `whisper-turbo` and `llm_model` is `gemma-cheap`

#### Scenario: Env override
- **GIVEN** `DETAILS_LIVE_NOTES_EVERY_MIN=0.5`
- **WHEN** settings load
- **THEN** the interval is 0.5

#### Scenario: Invalid
- **GIVEN** `live.notes_every_min` is 0 or 99
- **WHEN** settings load
- **THEN** startup fails naming `live.notes_every_min`

### Requirement: Brave end-to-end with a fake microphone
The system SHALL include an end-to-end test that runs in Brave with a `getUserMedia` override playing a generated wav, covering a live take with commits and a notes update, a stop with a saved recording and a live-preview run, and a take with LM Studio down that still saves, and MUST print a visible SKIP reason (through `sys.__stderr__` plus `pytest.skip`) when Brave or Playwright is unavailable; the tests are marked `e2e` and excluded from the default run.

#### Scenario: Happy path
- **GIVEN** the app with the fake worker and fake LM Studio
- **WHEN** the test toggles Live assist, starts, waits for a notes update and stops
- **THEN** the page shows lines and notes and the Saved card
- **AND** the recording duration matches the fed audio within 50 ms

#### Scenario: LM Studio down
- **GIVEN** the fake LM Studio refuses connections
- **WHEN** the same steps run
- **THEN** the page shows the unreachable message and the take still saves

#### Scenario: Missing tools
- **GIVEN** Brave is not installed
- **WHEN** the suite runs
- **THEN** the test is skipped with a printed reason
