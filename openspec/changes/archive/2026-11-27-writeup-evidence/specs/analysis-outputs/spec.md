## ADDED Requirements

### Requirement: Quotes are located in the audio

The system SHALL add `at_ms` at read time to every write-up item whose quote is found in the transcript segments, equal to the start of the segment where the normalised quote begins, and the Analyse page SHALL play the recording from `at_ms` when that quote is activated.

#### Scenario: Quote jumps to its moment

- **GIVEN** a decision whose quote lies in the segment starting at 161000 ms
- **WHEN** `GET /api/analyses/{group_id}` is requested
- **THEN** the item has `at_ms` 161000
- **AND** activating the quote on the page sets the player to 2:41 and plays

#### Scenario: Quote not in the transcript

- **GIVEN** an item with `verified: false`
- **WHEN** the analysis is requested
- **THEN** the item has no `at_ms` and its quote is shown as plain greyed text

### Requirement: Quote counts are always shown and honestly worded

The system SHALL show, for every write-up kind that has quotes, how many quotes were found in the transcript and how many were not, and SHALL state that a found quote proves the words were said and not that the point is right.

#### Scenario: All quotes found

- **GIVEN** a debrief with 9 quotes, all found
- **WHEN** its tab is opened
- **THEN** the line "9 quotes found in the transcript, 0 not found" is visible

### Requirement: Markdown keeps the evidence

The system SHALL render, after each Markdown item that has a quote, an indented line with the quote and, when located, its `[mm:ss]` time, and SHALL mark an item whose quote was not found with `quote not found`.

#### Scenario: Exported decision

- **GIVEN** a stored decision with a located quote
- **WHEN** its Markdown is requested
- **THEN** the item is followed by a line starting with `  > ` containing the quote and `[02:41]`

### Requirement: Analysis numbers are unique per recording

The system SHALL number analysis groups by their position among all analysis groups of the recording, oldest first.

#### Scenario: Two transcript runs

- **GIVEN** one analysis on transcript run #1 and a later one on transcript run #3
- **WHEN** both lists are requested
- **THEN** the labels start with `Analysis #1` and `Analysis #2`

### Requirement: Failed write-ups can be retried from the Write-up card

The system SHALL show the stored error for a failed or cancelled write-up kind and a Retry control for a failed one, and `GET /api/analyses/{group_id}/markdown` SHALL answer 409 with that error when the group has no outputs because it failed.

#### Scenario: Out of memory

- **GIVEN** an analysis that failed with "needs ~17 GB, 13.3 GB free"
- **WHEN** its tab is opened
- **THEN** that text and a Retry button are shown
- **AND** pressing Retry POSTs to `/api/jobs/{job_id}/retry`

### Requirement: The live box names the model that is writing

The system SHALL title the live reply box with the label of the model of the analysis being watched.

#### Scenario: Krikri is running

- **GIVEN** an analysis running on "Krikri 8B (Greek)"
- **WHEN** the live box is visible
- **THEN** its title is "Krikri 8B (Greek) is writing"
