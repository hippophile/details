# recording Specification

## Purpose
TBD - created by archiving change recording-capture. Update Purpose after archive.

## Requirements

### Requirement: Recording and script schema
The system SHALL provide migration `0002_recording.sql` creating tables `script` and `recording` as defined in the proposal, and SHALL NOT create any other table.

#### Scenario: Migration applies
- **GIVEN** a database at migration 0001
- **WHEN** the migration runner is executed
- **THEN** `script` and `recording` exist with the documented columns
- **AND** `schema_migration` records version 0002

#### Scenario: Slug uniqueness
- **GIVEN** a `script` row with slug `standup`
- **WHEN** another row with slug `standup` is inserted
- **THEN** the database rejects it

### Requirement: Seeded test scripts
The system SHALL load the 3 v1 tab scripts and the long eval script from `db/seeds/scripts/*.md` into the `script` table at startup, keyed by slug, and SHALL expose them via `GET /api/scripts` ordered by `sort_order`.

#### Scenario: First start
- **GIVEN** an empty `script` table
- **WHEN** the app starts
- **THEN** 4 rows exist and `GET /api/scripts` returns them in seed order with `id`, `name`, `body_md`

#### Scenario: Restart is idempotent
- **GIVEN** the 4 scripts already seeded
- **WHEN** the app starts again
- **THEN** there are still 4 rows and ids are unchanged

#### Scenario: Seed file edited
- **GIVEN** a seed file whose body changed
- **WHEN** the app starts
- **THEN** the row with that slug has the new body and new sha256

### Requirement: Record page controls
The Record page at `web/record/` SHALL keep the v1 look (same tokens, cards, pill mic button, max-width 820 px) and SHALL provide a title input, a mic start/stop button, an mm:ss timer, script tabs, a level meter and the shared nav.

#### Scenario: Default title
- **GIVEN** the page is open and the title input is empty
- **WHEN** the user starts recording
- **THEN** the recording title is "Recording YYYY-MM-DD HH:MM" in local time

#### Scenario: Timer
- **GIVEN** recording is running
- **WHEN** 65 s of audio have been sent
- **THEN** the timer shows 01:05

#### Scenario: Controls locked while recording
- **GIVEN** recording is running
- **WHEN** the user looks at the title input
- **THEN** it is disabled and the tabs remain usable

### Requirement: Test-script tabs
The Record page SHALL render one tab per row of `GET /api/scripts`, SHALL render each `**Name:**` line as a speaker line coloured by order of first appearance, and SHALL insert script text with `textContent` only (no HTML injection).

#### Scenario: Tabs follow the database
- **GIVEN** 4 scripts in the database
- **WHEN** the page loads
- **THEN** 4 tabs show, the first is active, and clicking another shows its text and hides the rest

#### Scenario: Keyboard
- **GIVEN** a tab has focus
- **WHEN** the user presses Right Arrow
- **THEN** the next tab is activated

#### Scenario: Selected script is stored
- **GIVEN** the "Bank BPMN review" tab is active
- **WHEN** a recording is started and saved
- **THEN** the `recording.script_id` is that script's id

#### Scenario: Markup in script
- **GIVEN** a script body containing `<img onerror=...>`
- **WHEN** it is rendered
- **THEN** it appears as literal text and nothing executes

### Requirement: Level meter, clip counter, dead-mic warning
The Record page SHALL show RMS level with peak colouring as in v1, SHALL count clip events (a 100 ms block with peak above -1 dBFS) and show "N clips", and SHALL show a dead-mic warning when peak stays below -60 dBFS for 10 consecutive seconds of recording.

#### Scenario: Clipping
- **GIVEN** recording is running
- **WHEN** 3 separate 100 ms blocks exceed -1 dBFS
- **THEN** the counter shows "3 clips" and the meter bar is red during those blocks

#### Scenario: Dead mic
- **GIVEN** recording is running
- **WHEN** peak stays below -60 dBFS for 10 s
- **THEN** a visible warning "No signal - is the mic muted?" appears
- **AND** it disappears when signal returns

#### Scenario: Clip count in Saved card
- **GIVEN** a take with 2 clip events
- **WHEN** it is saved
- **THEN** the Saved card shows "2 clips"

### Requirement: 16 kHz capture without resampling code
The Record page SHALL capture with `new AudioContext({sampleRate: 16000})`, mono, with echoCancellation, noiseSuppression and autoGainControl all false, SHALL NOT contain its own resampler, and the server SHALL reject a `start` whose `sample_rate` differs from `record.sample_rate`.

#### Scenario: Browser honours 16 kHz
- **GIVEN** the AudioContext reports sampleRate 16000
- **WHEN** the user starts recording
- **THEN** 100 ms blocks of 1600 int16 samples are sent

#### Scenario: Browser does not honour 16 kHz
- **GIVEN** the AudioContext reports sampleRate 48000
- **WHEN** the user presses start
- **THEN** recording does not start and the page shows an explanatory error

#### Scenario: Server rejects wrong rate
- **GIVEN** a WS client
- **WHEN** it sends `start` with `sample_rate` 48000
- **THEN** the server replies `error` and creates no folder

### Requirement: Crash-safe streaming write
The server SHALL create `${DETAILS_DATA_DIR}/recordings/<uuid>/` on `start`, append each received chunk to `rec.wav` immediately, patch the WAV header sizes and fsync at least every `settings.record.sync_interval_s` seconds, and keep `meta.json` (written atomically via temp file and rename) with status `recording`.

#### Scenario: File grows during take
- **GIVEN** a started session
- **WHEN** 3 chunks are sent
- **THEN** `rec.wav` contains their bytes after the 44-byte header and `meta.json` status is `recording`

#### Scenario: Header patched periodically
- **GIVEN** `sync_interval_s` set to 0.2 in the test config
- **WHEN** chunks are sent for 0.5 s and the process is killed without stop
- **THEN** `wave.open` reads a non-zero frame count within one interval of the last chunk

#### Scenario: Odd byte length
- **GIVEN** a binary message with an odd number of bytes
- **WHEN** it is received
- **THEN** the trailing byte is held and prepended to the next chunk, never written as half a sample

### Requirement: WebSocket security and limits
The WS endpoint SHALL pass through the foundation Origin check (a mismatched Origin on upgrade is rejected), SHALL set an explicit `max_msg_size` from `settings.record.max_msg_mb`, and all database access in recording code SHALL use the async connection pool.

#### Scenario: Foreign origin
- **GIVEN** a WS upgrade request with `Origin: http://evil.example`
- **WHEN** it reaches the server
- **THEN** it is rejected and no recording folder is created

#### Scenario: Oversized message
- **GIVEN** `max_msg_mb` 1
- **WHEN** a client sends a 2 MB binary message
- **THEN** the socket is closed and the take so far remains recoverable

### Requirement: Record settings section
The system SHALL read recording options from the `[record]` section (`settings.record.*`, env `DETAILS_RECORD_<KEY>`) and SHALL validate them in the section's validation hook in `settings.py`.

#### Scenario: Env override
- **GIVEN** `DETAILS_RECORD_GRACE_S=5`
- **WHEN** settings load
- **THEN** `settings.record.grace_s` is 5

#### Scenario: Invalid value
- **GIVEN** `record.sample_rate` is 0
- **WHEN** settings load
- **THEN** startup fails with a message naming `record.sample_rate`

### Requirement: Preflight checks
The server SHALL refuse `start` with a readable error when ffmpeg or ffprobe cannot be found, or when free space in the data dir is below `record.min_free_gb`.

#### Scenario: ffmpeg missing
- **GIVEN** the configured ffmpeg path does not exist and `which` finds none
- **WHEN** the client sends `start`
- **THEN** the server replies `error` naming ffmpeg and creates no folder

#### Scenario: Low disk
- **GIVEN** free space below `record.min_free_gb`
- **WHEN** the client sends `start`
- **THEN** the server replies `error` with the free and required GB

### Requirement: Finalize to FLAC master
On `stop` the server SHALL patch the header, convert `rec.wav` to `master.flac` with ffmpeg, verify the FLAC duration against the WAV data length within 50 ms, compute `sha256` of the master, insert the `recording` row (duration_ms, sample_rate, channels, codec `flac`, container `flac`, audio_sha256, size_bytes, script_id, source `{"capture":"mic"}`), and delete `rec.wav` unless `record.keep_wav` is true.

#### Scenario: Normal stop
- **GIVEN** 5 s of audio streamed
- **WHEN** the client sends `stop`
- **THEN** `master.flac` is 16000 Hz mono with duration 5.0 s +/- 0.05
- **AND** the row exists with `audio_path` `recordings/<uuid>/master.flac`
- **AND** `rec.wav` is gone and `meta.json` status is `done`
- **AND** the server replies `saved` with id, title, duration_ms, size_bytes

#### Scenario: Keep WAV
- **GIVEN** `record.keep_wav` true
- **WHEN** a take is stopped
- **THEN** both `rec.wav` and `master.flac` exist

#### Scenario: FLAC conversion fails
- **GIVEN** ffmpeg exits non-zero or the duration check fails
- **WHEN** the client sends `stop`
- **THEN** `rec.wav` is kept, the row points at it with container `wav`, codec `pcm_s16le`
- **AND** `source.flac_failed` is true and `saved` carries a warning

#### Scenario: Empty take
- **GIVEN** less than 0.5 s of audio
- **WHEN** the client sends `stop`
- **THEN** the folder is removed, no row is inserted, and the server replies `empty`

### Requirement: Saved card
After a successful stop the Record page SHALL show a "Saved" card with title, duration, size and clip count and three actions: "Analyse now" (link to `/analyse?rec=<id>`), "Open library" (link to `/library`), and "New recording" (resets the page).

#### Scenario: Saved card actions
- **GIVEN** a saved recording with id X
- **WHEN** the card is shown
- **THEN** "Analyse now" has href `/analyse?rec=X` and "Open library" has href `/library`

#### Scenario: New recording
- **GIVEN** the Saved card is visible
- **WHEN** the user presses "New recording"
- **THEN** the card hides, timer is 00:00, title input is empty and enabled

#### Scenario: Save warning
- **GIVEN** the server replied `saved` with a FLAC-failed warning
- **WHEN** the card is shown
- **THEN** the warning text is visible on the card

### Requirement: WebSocket drop grace and resume
When the WS closes without `stop`, the server SHALL mark the session `interrupted`, SHALL accept `resume{recording_id}` for `record.grace_s` seconds and keep appending, and SHALL finalize automatically once the grace expires. The page SHALL buffer unsent chunks (max 10 minutes) and reconnect automatically.

#### Scenario: Reconnect inside grace
- **GIVEN** a session whose socket dropped 1 s ago
- **WHEN** a client sends `resume` with the id and 2 more chunks
- **THEN** the file contains all chunks in order and the session continues

#### Scenario: Tab closed
- **GIVEN** a session whose socket dropped and no resume arrives
- **WHEN** `grace_s` passes
- **THEN** the take is finalized, a row exists with `source.interrupted` true, and `meta.json` status is `done`

#### Scenario: Resume after grace
- **GIVEN** a session already finalized by grace expiry
- **WHEN** a client sends `resume`
- **THEN** the server replies `error` "already finalized" and writes nothing

### Requirement: Crash recovery at startup
At startup the server SHALL scan `recordings/*/meta.json` for status `recording` or `finalizing`, repair the WAV header from the file size truncated to whole frames, finalize it as in the stop path, and mark it `recovered` (title suffix " (recovered)", `source.recovered` true). Folders with zero audio frames SHALL be removed.

#### Scenario: Killed server
- **GIVEN** a folder with `meta.json` status `recording` and a `rec.wav` with header size 0 but 3 s of data
- **WHEN** the app starts
- **THEN** a row exists with duration 3000 ms +/- 50, `master.flac` exists, and meta status is `recovered`

#### Scenario: Torn last sample
- **GIVEN** a `rec.wav` whose data length is odd
- **WHEN** recovery runs
- **THEN** the last byte is dropped and the file is valid

#### Scenario: Nothing recorded
- **GIVEN** a `recording` folder with no audio frames
- **WHEN** recovery runs
- **THEN** the folder is deleted and no row is inserted

#### Scenario: Finished recordings untouched
- **GIVEN** a folder with status `done`
- **WHEN** recovery runs
- **THEN** it is not modified

### Requirement: Live preview deferred
The Record page SHALL NOT include live transcription in this change and SHALL show a link to the v1 page at `/live` labelled for live text; `/live` SHALL keep working unchanged.

#### Scenario: Link present
- **GIVEN** the Record page is open
- **WHEN** the user looks for live text
- **THEN** a link to `/live` is visible

#### Scenario: v1 intact
- **GIVEN** the new routes are registered
- **WHEN** `/live` is requested
- **THEN** it returns the v1 page with HTTP 200

### Requirement: v1 session importer
The system SHALL provide `python -m details.import_v1 [--dir sessions] [--dry-run]` that imports each `<YYYYMMDD-HHMMSS>.wav` as a recording with `master.flac`, started_at parsed from the filename in `DETAILS_TZ` (default Europe/Athens), `source` `{"import":"v1","wav_sha256":...}`, copies the `.txt` to `v1-transcript.txt` beside it, skips any wav whose sha256 is already imported, leaves the source directory untouched, and prints an imported/skipped/failed table.

#### Scenario: Dry run
- **GIVEN** `sessions/` with 1 valid wav
- **WHEN** run with `--dry-run`
- **THEN** the table shows 1 would-import and no folder, row, or file is created

#### Scenario: Real run then re-run
- **GIVEN** the dry run looked right
- **WHEN** run twice without `--dry-run`
- **THEN** the first run imports 1, the second reports 1 skipped and 0 imported
- **AND** `sessions/*.wav` is byte-identical to before

#### Scenario: Bad file
- **GIVEN** a `.wav` that is not valid audio
- **WHEN** the importer runs
- **THEN** it is listed as failed, other files still import, and the exit code is 1
