# recording Specification

## MODIFIED Requirements

### Requirement: Live preview deferred
The Record page SHALL show live transcription and rolling notes only when the user has turned on "Live assist" (capability `live-assistant`), SHALL otherwise behave exactly as before, and SHALL keep a link to `/live` labelled for live text; `/live` SHALL keep working unchanged.

#### Scenario: Link present
- **GIVEN** the Record page is open
- **WHEN** the user looks for live text
- **THEN** a link to `/live` is visible next to the Live assist toggle

#### Scenario: v1 intact
- **GIVEN** the new routes are registered
- **WHEN** `/live` is requested
- **THEN** it returns the v1 page with HTTP 200

#### Scenario: Live off is the old behaviour
- **GIVEN** Live assist is not turned on
- **WHEN** a take is made
- **THEN** no live message, worker or lease is involved
