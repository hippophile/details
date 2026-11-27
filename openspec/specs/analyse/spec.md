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

### Requirement: Review-first card order

The Analyse page SHALL place the Write-up card and then the Transcript card directly after the recording player and running-job cards, before the cards that start runs.

#### Scenario: Opening a recording with a write-up

- **GIVEN** a recording with a done transcript and a done analysis
- **WHEN** the Analyse page loads
- **THEN** the Write-up card comes before the Transcript card, and both come before the New run card

### Requirement: Paragraph view of the transcript

The Analyse page SHALL group transcript segments into paragraphs by speaker change, silence and sentence end, show them by default with a Paragraphs / Lines toggle that is remembered, and keep click-to-seek for every segment inside a paragraph.

#### Scenario: Speaker change starts a paragraph

- **GIVEN** three segments by speaker 1 followed by two by speaker 2, without long gaps
- **WHEN** the paragraph view renders
- **THEN** there are two paragraphs, the second starting at the fourth segment's time

#### Scenario: Seeking inside a paragraph

- **GIVEN** a paragraph of five segments
- **WHEN** the user activates the third segment
- **THEN** the player seeks to the third segment's start

#### Scenario: Lines view is unchanged

- **GIVEN** the user switches to Lines
- **WHEN** the page is reloaded
- **THEN** one row per segment is shown, as before this change

### Requirement: Speaker setup text is collapsed

The Analyse page SHALL show a one-line notice and a closed "How to set it up" disclosure, holding the reason and hint from the API, when the speaker model is unavailable.

#### Scenario: Model not downloaded

- **GIVEN** the speaker model is not downloaded
- **WHEN** the Who talked card renders
- **THEN** "Speaker detection is not set up." is visible and the setup steps are inside a closed disclosure
