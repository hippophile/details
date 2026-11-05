## ADDED Requirements

### Requirement: Start, Pause/Continue and End buttons
The Record page SHALL offer three controls: Start, a single Pause button that becomes Continue while paused, and End; Pause and End SHALL be enabled whenever a recording id exists and the page is not finishing (also during a reconnect), and Start SHALL be disabled while a recording is open.

#### Scenario: Pause toggles to Continue
- **GIVEN** a recording with a recording id
- **WHEN** the user presses Pause
- **THEN** the button reads "Continue", the timer stops and the status reads "Paused: nothing is recorded"
- **AND** pressing Continue restores the "Pause" label and the timer runs again

#### Scenario: End while paused saves the take
- **GIVEN** a paused recording with 6 s of audio
- **WHEN** the user presses End
- **THEN** the take is saved like a normal stop with a duration of about 6 s

### Requirement: Paused audio is not recorded
While paused the page MUST NOT queue or send audio blocks, and the server SHALL keep one continuous WAV in which the paused time is absent, storing each pause as `{at_frames, wall_paused_ms}` in `meta.json` and in `recording.source.pauses`.

#### Scenario: Pause excluded from duration
- **GIVEN** 6 s recorded, then a 3 s pause, then 4 s recorded
- **WHEN** the user presses End
- **THEN** the saved `duration_ms` is about 10000 (±300 ms)
- **AND** `source.pauses` has exactly one entry with `at_frames` at about 6 s

#### Scenario: Control messages do not collide with reconnect
- **GIVEN** the WS message `resume` already means "reconnect to an open recording"
- **WHEN** the page pauses and continues
- **THEN** it sends `{"type":"pause"}` and `{"type":"continue"}`
- **AND** a duplicate `pause`, a `continue` without a prior `pause`, or either without a session is silently ignored (no `error` message, the take continues)

#### Scenario: No false gap during a pause
- **GIVEN** a recording paused for 3 s
- **WHEN** no audio blocks arrive during the pause
- **THEN** the page sends no `gap` message, during the pause or right after Continue

#### Scenario: Reconnect keeps the paused state
- **GIVEN** a paused recording whose WS connection drops and reconnects with `resume`
- **WHEN** the server answers `ready` with `paused: true`
- **THEN** the page shows "Paused" and does not send `pause` again
- **AND** `source.pauses` still has exactly one entry

#### Scenario: Blocks before a pause arrive before it
- **GIVEN** a disconnected WS with audio blocks still queued
- **WHEN** the user presses Pause and the WS reconnects
- **THEN** the queued blocks are sent first and the `pause` after them, so `at_frames` includes those blocks

#### Scenario: Pauses survive a crash
- **GIVEN** a recording paused once and then the server is killed
- **WHEN** the server restarts and recovers the take
- **THEN** the recovered recording keeps the pause in `source.pauses`, closed with `open: true` if it was still paused

### Requirement: Live assist follows pause
When live assist is on, pausing SHALL flush the pending speech as a live commit and pause the rolling-notes timer, and Continue SHALL resume both; a live failure MUST NOT affect pausing or continuing the recording.

#### Scenario: Timestamps stay right after a pause
- **GIVEN** a pause while less than 0.3 s of speech is pending
- **WHEN** speech continues after Continue
- **THEN** the next live commit's start_ms matches its real position in the recording

#### Scenario: Pause flushes the last sentence
- **GIVEN** live assist on and 2 s of speech not yet committed
- **WHEN** the user presses Pause
- **THEN** a `live_commit` for that speech arrives
- **AND** no rolling-notes update starts while paused
