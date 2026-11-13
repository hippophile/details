# analyse Specification

## Purpose
TBD - created by archiving change copy-transcript. Update Purpose after archive.

## Requirements

### Requirement: Copy the whole transcript
The Analyse page SHALL offer a Copy transcript button that copies the whole selected transcript as plain text, one line per segment, without changing any stored data; timestamps SHALL be included only when the Timestamps tick-box is on.

#### Scenario: Plain copy
- **GIVEN** a done run with segments "Καλημέρα" at 0 s and "ok" at 65 s
- **WHEN** the user presses Copy transcript with Timestamps off
- **THEN** the clipboard holds "Καλημέρα\nok"

#### Scenario: With timestamps and speakers
- **GIVEN** the same run with a speaker run picked
- **WHEN** Timestamps is on and the user presses Copy transcript
- **THEN** each line reads "[mm:ss] Name: text"

#### Scenario: Clipboard refused
- **WHEN** the browser rejects the clipboard write
- **THEN** the status reads "Copy failed: …" and nothing else changes
