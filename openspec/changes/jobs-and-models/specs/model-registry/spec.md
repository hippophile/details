## ADDED Requirements

### Requirement: Config-driven model registry
The system SHALL load all speech and LLM models from `models.toml`, shipping exactly the entries `whisper-turbo` and `whisper-large` (engine `mlx_whisper`) and `gemma-cheap` and `gemma-nice` (engine `lmstudio`), and SHALL contain no model identifiers in code or page JavaScript.

#### Scenario: Shipped entries load
- **GIVEN** the repository `models.toml`
- **WHEN** the registry loads
- **THEN** it exposes STT ids `whisper-turbo`, `whisper-large` and LLM ids `gemma-cheap`, `gemma-nice`
- **AND** `gemma-cheap` has role `cheap` and `gemma-nice` has role `nice`

#### Scenario: New model is one block
- **GIVEN** a `models.toml` with an extra `[llm.test-model]` block
- **WHEN** the registry loads
- **THEN** `test-model` is listed with no code change

#### Scenario: Unknown engine id
- **GIVEN** an STT entry with `engine = "whisperx"` or an LLM entry with `engine = "mlx_whisper"`
- **WHEN** the registry loads
- **THEN** loading fails with a message naming the entry and the allowed engine ids

#### Scenario: Invalid entry named
- **GIVEN** an STT entry with an unknown `engine` or a missing `repo`
- **WHEN** the registry loads
- **THEN** loading fails with an error naming that entry

### Requirement: Models API with installed check
The system SHALL expose `GET /api/models` returning `{stt, llm, lmstudio:{reachable}}` with every entry carrying `id`, `kind`, `label`, `engine`, `role`, `needs_gb`, `installed` and optional `unavailable_reason` and `hint`, accepting `?kind=stt|llm` for a flat list, where STT `installed` means weights exist in the Hugging Face cache and LLM `installed` means the model key is downloaded in LM Studio, using `settings.lmstudio.url`.

#### Scenario: Installed and missing
- **GIVEN** the cache has weights for `whisper-turbo` only
- **WHEN** a client requests `GET /api/models`
- **THEN** `whisper-turbo` has `installed: true` and `whisper-large` has `installed: false`

#### Scenario: Kind filter
- **GIVEN** the shipped registry
- **WHEN** a client requests `GET /api/models?kind=llm`
- **THEN** the response is a flat list of the 2 LLM entries

#### Scenario: Entry shape
- **GIVEN** the shipped registry
- **WHEN** a client requests `GET /api/models`
- **THEN** each entry has `id, kind, label, engine, role, needs_gb, installed`
- **AND** `role` is one of `cheap, nice, fast, accurate`

#### Scenario: Unknown model id
- **GIVEN** no registry entry `nope`
- **WHEN** a client posts a request naming model id `nope`
- **THEN** the response is 422

#### Scenario: LM Studio unreachable
- **GIVEN** LM Studio is not running
- **WHEN** a client requests `GET /api/models`
- **THEN** the response is 200
- **AND** LLM entries have `installed: false` and the body reports `lmstudio.reachable` as false

### Requirement: LM Studio controller
The system SHALL list downloaded models, list loaded models, load a model with an identifier `details-<registry id>` and configured context length, and unload by identifier, through a replaceable transport, taking the `lms` binary from `settings.lmstudio.cli`.

#### Scenario: Load uses app identifier
- **GIVEN** registry entry `gemma-nice` with context 32768
- **WHEN** the controller loads it
- **THEN** the transport receives the model key, identifier `details-gemma-nice` and context 32768

#### Scenario: Binary from settings
- **GIVEN** `settings.lmstudio.cli` is `/opt/x/lms`
- **WHEN** the CLI transport builds a command
- **THEN** the first argument is `/opt/x/lms`

#### Scenario: Parse recorded output
- **GIVEN** a recorded `lms ps --json` sample from the installed version
- **WHEN** the controller lists loaded models
- **THEN** it returns identifier and model key for each

### Requirement: Memory manager
The system SHALL unload app-loaded LM Studio models before loading another model or starting a speech step, SHALL NOT unload models it did not load unless `lmstudio.exclusive` is true, and SHALL fail a step with a message stating needed and free GB when memory is short after unloading its own models.

#### Scenario: Unload before load
- **GIVEN** `details-gemma-cheap` is loaded by the app
- **WHEN** the manager prepares `gemma-nice`
- **THEN** the controller records unload of `details-gemma-cheap` before load of `details-gemma-nice`

#### Scenario: Foreign model untouched
- **GIVEN** a loaded model with identifier `my-chat-model` (no `details-` prefix) and `exclusive` false
- **WHEN** the manager releases memory for a speech step
- **THEN** no unload is issued for `my-chat-model`

#### Scenario: Not enough memory
- **GIVEN** a fake memory reading of 9 GB free and an entry needing 14 GB
- **WHEN** the manager runs preflight
- **THEN** the step fails with a message containing "needs ~14 GB" and "9 GB free"
