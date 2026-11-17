## MODIFIED Requirements

### Requirement: Page routes
The system SHALL serve `/record`, `/library` and `/analyse` from `web/<x>/index.html` with assets under `/<x>/`, SHALL redirect `/` to `/library`, and SHALL NOT expose `*.html` URLs.

#### Scenario: Pages served
- **GIVEN** stub `web/<x>/index.html` files
- **WHEN** a client requests `/record`, `/library` and `/analyse?rec=abc`
- **THEN** each returns 200 with that file's body

#### Scenario: No html URL
- **GIVEN** the same stubs
- **WHEN** a client requests `/record.html`
- **THEN** the response is 404

#### Scenario: Root opens the Library
- **GIVEN** the app is running
- **WHEN** a client requests `GET /` without following redirects
- **THEN** the response is 302 with `Location: /library`

### Requirement: Local-only request guard
The system SHALL reject requests whose `Host` is not localhost, 127.0.0.1 or [::1], and SHALL reject POST, PATCH, DELETE and WebSocket upgrade requests whose `Origin` does not match the request host, with 403.

#### Scenario: Foreign Host
- **GIVEN** a request with `Host: evil.example`
- **WHEN** it reaches the app
- **THEN** the response is 403

#### Scenario: Cross-origin write
- **GIVEN** a POST with `Origin: http://evil.example`
- **WHEN** it reaches the app
- **THEN** the response is 403 and no handler ran

#### Scenario: Cross-origin WebSocket
- **GIVEN** a WebSocket upgrade to `/api/record/ws` with a foreign `Origin`
- **WHEN** it reaches the app
- **THEN** the upgrade is refused with 403

#### Scenario: Same origin allowed
- **GIVEN** a POST with `Origin: http://127.0.0.1:8765` and `Host: 127.0.0.1:8765`
- **WHEN** it reaches the app
- **THEN** it is handled normally

## REMOVED Requirements

### Requirement: v1 live page preserved at /live
**Reason**: Owner made Library/Record/Analyse the main app (2026-11-17); the v1 test page is archived.
**Migration**: Git tag `archive/live-v1` holds the page and its `/models` and `/ws` endpoints; `/live`, `/models` and `/ws` now return 404.
