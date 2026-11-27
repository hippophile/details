## ADDED Requirements

### Requirement: Extra spoken languages per run

The system SHALL accept an optional `also` list of language codes on `POST /api/recordings/{id}/stt-runs`, validate it against `[stt] extra_languages`, store it in the run settings, and decode each window in the primary language unless one of the extra languages holds at least `[stt] switch_min_share` of the probability among the allowed languages.

#### Scenario: Window stays in the primary language

- **GIVEN** a run with `language: "el"`, `also: ["en","de"]` and `switch_min_share` 0.7
- **WHEN** a window's shares are el 0.35, en 0.60, de 0.05
- **THEN** the window is decoded in `el`
- **AND** the window record stores `language: "el"` and the three shares

#### Scenario: Window switches

- **GIVEN** the same run
- **WHEN** a window's shares are el 0.10, en 0.05, de 0.85
- **THEN** the window is decoded in `de`

#### Scenario: Bad request

- **GIVEN** `extra_languages = "en,de"`
- **WHEN** the client POSTs `also: ["is"]`, or `also` together with `language: "auto"`
- **THEN** the response is 422 with a message naming the problem

### Requirement: Run language is stored and labelled as requested

The system SHALL store, for an Auto run, the language of the most windows and the per-language window counts in `raw.languages`, and SHALL include the requested language setting in every run label.

#### Scenario: Auto run is labelled Auto

- **GIVEN** a done Auto run whose windows were 18 `el` and 13 `en`
- **WHEN** the runs list is requested
- **THEN** its `language` is `el`, its `languages` is `{"el": 18, "en": 13}` and its label contains `Auto`

#### Scenario: Two settings, two labels

- **GIVEN** two done runs of the same model, one `el`, one `el` with `also: ["de"]`
- **WHEN** the runs list is requested
- **THEN** the labels differ: one contains `Ελληνικά`, the other `Ελληνικά + de`

### Requirement: Analyse opens on the accurate run

The system SHALL return `model_role` and `cleaned` on each run, and the Analyse page SHALL open the newest done, non-live-preview run that is `accurate` and cleaned, else the newest cleaned run, else the first done run.

#### Scenario: Accurate run preferred

- **GIVEN** a newer done cleaned `fast` run and an older done cleaned `accurate` run
- **WHEN** the Analyse page loads
- **THEN** the `accurate` run is selected

#### Scenario: Old uncleaned accurate run is not preferred

- **GIVEN** an older done `accurate` run without cleaning and a newer done cleaned `fast` run
- **WHEN** the Analyse page loads
- **THEN** the cleaned `fast` run is selected
