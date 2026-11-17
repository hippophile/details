## ADDED Requirements

### Requirement: Live preview opt-in without v1 link
The Record page SHALL show live transcription and rolling notes only when the user has turned on "Live assist" (capability `live-assistant`), SHALL otherwise behave exactly as before, and SHALL NOT link to the archived `/live` page.

#### Scenario: No v1 link
- **GIVEN** the Record page is open
- **WHEN** its HTML is inspected
- **THEN** it contains no link to `/live`

#### Scenario: v1 gone
- **GIVEN** the app is running
- **WHEN** `/live` is requested
- **THEN** the response is 404

#### Scenario: Live off is the old behaviour
- **GIVEN** Live assist is not turned on
- **WHEN** a take is made
- **THEN** no live message, worker or lease is involved

## REMOVED Requirements

### Requirement: Live preview deferred
**Reason**: It required a link to `/live` and `/live` to keep working; the v1 page is archived (2026-11-17).
**Migration**: Replaced by "Live preview opt-in without v1 link", which keeps the opt-in rule and drops the link.
