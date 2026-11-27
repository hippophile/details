## ADDED Requirements

### Requirement: Brief output kind

The system SHALL offer an output kind `brief` with schema `schemas/brief.v1.json`, containing `kind_of_recording`, `summary`, `chapters`, `decisions`, `disagreements` with two or more positions each, `action_items` and `open_questions`, each item with a verbatim `quote`, and SHALL list it first among the kinds.

#### Scenario: A lesson has no decisions

- **GIVEN** a transcript of two people practising German verb forms
- **WHEN** the model returns `kind_of_recording: "lesson"` with empty `decisions`, `disagreements` and `action_items`
- **THEN** the output is valid and stored
- **AND** the page shows "Nothing was decided."

#### Scenario: Position without a second side

- **GIVEN** a disagreement with one position
- **WHEN** it is validated
- **THEN** it fails

### Requirement: Final decisions are linked by the model and assembled by code

The system SHALL number decisions and disagreements in transcript order, ask the model only which decision replaces which earlier decisions and which decision settles which disagreement, validate that reply against the numbered items, and write `status`, `replaces`, `replaced_by` and `settled_by` in code without changing any item's text.

#### Scenario: A later decision replaces an earlier one

- **GIVEN** decisions d1 "ship to all branches" at 0:35 and d2 "ship to 2 branches first" at 2:41
- **WHEN** the link reply is `{"decisions":[{"id":"d2","replaces":["d1"]}],"disagreements":[]}`
- **THEN** d2 is stored `final` with `replaces: ["d1"]` and d1 is stored `replaced` with `replaced_by: "d2"`
- **AND** the page shows d1 under d2 as "changed from"

#### Scenario: Link reply points forward

- **GIVEN** the same decisions
- **WHEN** the link reply says d1 replaces d2
- **THEN** it is a validation error and the call is retried once with that error

#### Scenario: Link fails twice

- **GIVEN** a brief with three decisions
- **WHEN** the link reply is invalid after its retry
- **THEN** the brief is stored with every decision `final`, no links and `links: "failed"`
- **AND** the page says the replaced decisions could not be worked out

#### Scenario: Long meeting

- **GIVEN** a transcript processed in three chunks, with a decision in chunk 1 and its change in chunk 3
- **WHEN** the brief is generated
- **THEN** the link step runs once on the merged decisions and marks the chunk-1 decision `replaced`

### Requirement: Foreign-script check

The system SHALL provide a prompt check `known_scripts` that rejects any model-written string outside `quote` containing letters that are not Greek or Latin, as a validation error naming the field.

#### Scenario: Bengali letters inside a Greek word

- **GIVEN** a brief whose summary contains "ορθোগ্রাফία"
- **WHEN** the check runs
- **THEN** it returns an error for `summary`
- **AND** a quote containing the same text is not an error

### Requirement: Brief Markdown and page

The system SHALL render the brief as sections Decided, Disagreed, To do, Open questions and Chapters, showing replaced decisions under their replacement, each disagreement as still open or settled by a named decision, and an explicit empty line for each empty section.

#### Scenario: Exported brief

- **GIVEN** a stored brief with one final decision that replaces another and one open disagreement
- **WHEN** its Markdown is requested
- **THEN** it equals `tests/golden/brief.md`, with a `changed from:` line and `still open`
