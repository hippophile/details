## ADDED Requirements

### Requirement: Layered settings
The system SHALL load a single immutable `Settings` object with nested sections (`settings.<section>.<key>`) from `config.toml`, overridden by `DETAILS_<SECTION>_<KEY>` environment variables validated by a per-section hook, with `.env` supplying values that are not already in the environment, and SHALL read no configuration from the environment anywhere else.

#### Scenario: Env overrides file
- **GIVEN** `config.toml` sets `port = 8765`
- **WHEN** `DETAILS_PORT=9000` is set and settings load
- **THEN** `settings.server.port` is 9000
- **AND** `settings.paths.data_dir` defaults to `~/details-data` expanded to an absolute path

#### Scenario: .env does not override the real environment
- **GIVEN** `.env` contains `DATABASE_URL=postgresql://a/details` and the environment has `DATABASE_URL=postgresql://b/details`
- **WHEN** settings load
- **THEN** `settings.db.url` is the environment value

#### Scenario: Bad config fails clearly
- **GIVEN** `config.toml` contains invalid TOML
- **WHEN** settings load
- **THEN** loading fails with an error that names the file

#### Scenario: Nested LM Studio section
- **GIVEN** `config.toml` has `[lmstudio]` with `url` and `cli`
- **WHEN** `DETAILS_LMSTUDIO_URL=http://localhost:5555` is set and settings load
- **THEN** `settings.lmstudio.url` is `http://localhost:5555` and `settings.lmstudio.cli` is the file value

#### Scenario: Validation hook
- **GIVEN** `DETAILS_SERVER_PORT=notanumber`
- **WHEN** settings load
- **THEN** loading fails naming `server.port`

### Requirement: Secrets never logged
The system SHALL NOT include database URLs, passwords or `.env` values in log lines or error messages.

#### Scenario: Bad DSN redacted
- **GIVEN** `DATABASE_URL=postgresql://u:s3cret@127.0.0.1:1/x`
- **WHEN** the app tries to connect and fails
- **THEN** the raised error text and captured logs do not contain `s3cret`

### Requirement: Application factory
The system SHALL provide `create_app(settings)` returning an aiohttp application that serves `/api/health` as JSON and `web/shared/` at `/shared/`, SHALL use an `AsyncConnectionPool` for all server DB access, and SHALL bind only to 127.0.0.1 when run.

#### Scenario: Health endpoint
- **GIVEN** an app created with test settings
- **WHEN** a client requests `GET /api/health`
- **THEN** the response is 200 JSON with `{"ok": true}`

#### Scenario: Local-only binding
- **GIVEN** default settings
- **WHEN** `python -m details` starts
- **THEN** the listening host is 127.0.0.1 and the resolved port is printed

#### Scenario: Pending migration
- **GIVEN** a database with unapplied migration files
- **WHEN** the app starts
- **THEN** startup fails with a message containing "run `make migrate`"
- **AND** no startup hook has run

#### Scenario: No database configured
- **GIVEN** `DATABASE_URL` is unset
- **WHEN** the app starts
- **THEN** DB hooks are skipped and `GET /api/health` returns 200 with `db` false

#### Scenario: Hook order
- **GIVEN** hooks registered as recovery, seed, runner
- **WHEN** the app starts on a migrated database
- **THEN** they run in that order, after the migration check

### Requirement: Page routes
The system SHALL serve `/record`, `/library` and `/analyse` from `web/<x>/index.html` with assets under `/<x>/`, and SHALL NOT expose `*.html` URLs.

#### Scenario: Pages served
- **GIVEN** stub `web/<x>/index.html` files
- **WHEN** a client requests `/record`, `/library` and `/analyse?rec=abc`
- **THEN** each returns 200 with that file's body

#### Scenario: No html URL
- **GIVEN** the same stubs
- **WHEN** a client requests `/record.html`
- **THEN** the response is 404

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
- **GIVEN** a WebSocket upgrade to `/ws` with a foreign `Origin`
- **WHEN** it reaches the app
- **THEN** the upgrade is refused with 403

#### Scenario: Same origin allowed
- **GIVEN** a POST with `Origin: http://127.0.0.1:8765` and `Host: 127.0.0.1:8765`
- **WHEN** it reaches the app
- **THEN** it is handled normally

### Requirement: v1 live page preserved at /live
The system SHALL serve the unchanged v1 live page at `/live`, with its `/models` and `/ws` endpoints still working at the root, and SHALL NOT change v1 recording or transcription behaviour.

#### Scenario: Page identical to v1
- **GIVEN** the app is running
- **WHEN** a client requests `GET /live`
- **THEN** the body is byte-identical to the v1 `index.html` file at `web/live-v1/index.html`

#### Scenario: Models endpoint
- **GIVEN** a fake Hugging Face hub directory containing one `whisper-*` model with weights
- **WHEN** a client requests `GET /models`
- **THEN** the response is a JSON list with that model repo name

### Requirement: Database connection and migrations
The system SHALL connect to PostgreSQL with psycopg 3 using `DATABASE_URL`, and SHALL apply `db/migrations/NNNN_name.sql` files in numeric order, each in its own transaction, recording each in a `schema_migration` table with its checksum.

#### Scenario: Apply and skip
- **GIVEN** an empty database and migration `0001_schema_migration.sql`
- **WHEN** the runner runs twice
- **THEN** `schema_migration` has exactly one row for version 1
- **AND** the second run applies nothing

#### Scenario: Edited migration rejected
- **GIVEN** migration 0001 was applied
- **WHEN** its file content changes and the runner runs
- **THEN** the runner fails naming the migration and applies nothing further

#### Scenario: Failed migration rolls back
- **GIVEN** a migration file containing invalid SQL after a valid statement
- **WHEN** the runner runs
- **THEN** none of that file's statements persist and no `schema_migration` row is written

### Requirement: Isolated test database
The test suite SHALL reset the schema of the database in `DATABASE_URL_TEST` at the start of each run, SHALL refuse to do so if the database name does not end in `_test`, and SHALL skip database tests with a stated reason when `DATABASE_URL_TEST` is unset.

#### Scenario: Truncate between tests
- **GIVEN** a test inserted rows into any table
- **WHEN** the next DB test starts
- **THEN** every table except `schema_migration` is empty and identities restart

#### Scenario: Reset on each run
- **GIVEN** a leftover table in `details_test`
- **WHEN** a new test run starts
- **THEN** the table is gone and migrations are applied fresh

#### Scenario: Guard against the real database
- **GIVEN** `DATABASE_URL_TEST` points at a database named `details`
- **WHEN** the fixture starts
- **THEN** it raises an error and executes no `DROP`

### Requirement: Makefile verbs
The Makefile SHALL provide `setup`, `run`, `test` and `migrate`, where `test` excludes tests marked `gpu`.

#### Scenario: Default test run needs no GPU
- **GIVEN** a test marked `@pytest.mark.gpu`
- **WHEN** `make test` runs
- **THEN** that test is deselected

### Requirement: Shared page shell
The system SHALL provide `web/shared/shared.css` defining the v1 colour tokens with light and dark variants, and `nav.js` rendering a navigation bar with entries Record, Library and Analyse, highlighting the current page.

#### Scenario: Nav entries
- **GIVEN** `nav.js` is loaded on a page at `/library`
- **WHEN** it renders
- **THEN** the bar contains links Record, Library and Analyse in that order
- **AND** the Library link is marked current

#### Scenario: Tokens present
- **GIVEN** `shared.css`
- **WHEN** inspected
- **THEN** it defines `--bg --card --fg --muted --line --good --warn --bad --accent` and a `prefers-color-scheme: dark` override
