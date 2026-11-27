## ADDED Requirements

### Requirement: Transcript cleaning before storage

The system SHALL clean a finished run's segments before storing them: drop segments containing a known Whisper junk phrase, collapse repeat loops, cap end times at the audio length, order segments and trim overlaps, and store the counts of each fix in `stt_run.raw["cleaned"]`.

#### Scenario: Junk phrase is removed

- **GIVEN** a window returns the segment "Υπότιτλοι AUTHORWAVE"
- **WHEN** the run is stored
- **THEN** no stored segment contains that text
- **AND** `raw.cleaned.junk` is 1

#### Scenario: Repeat loop is collapsed, real repetition is kept

- **GIVEN** one segment repeats "Αφικού" 50 times and another segment is "Gehelfen Gehelfen"
- **WHEN** the run is stored
- **THEN** the first segment holds "Αφικού" once
- **AND** the second segment is stored unchanged

#### Scenario: Times stay inside the audio and in order

- **GIVEN** a 919.8 s recording, a segment ending at 929.98 s, and a segment starting before the previous one ends
- **WHEN** the run is stored
- **THEN** no segment ends after 919.8 s
- **AND** every segment starts at or after the previous segment's end

### Requirement: Overlapping transcription windows

The system SHALL decode each window with `[stt] overlap_s` seconds of extra audio on each side (clipped to the recording) and keep only the segments whose midpoint lies inside the window's own range, so a segment at a join is stored exactly once.

#### Scenario: Segment at a join is stored once

- **GIVEN** `window_s = 30` and `overlap_s = 3`
- **WHEN** window 1 is transcribed
- **THEN** the engine is asked for 27 s to 63 s
- **AND** a segment from 29 s to 32 s is stored by window 0 only

#### Scenario: Old job resumes unchanged

- **GIVEN** a job whose saved spec has no `overlap_s`
- **WHEN** it resumes
- **THEN** windows are decoded without overlap, as before
