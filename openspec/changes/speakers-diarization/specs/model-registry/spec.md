# model-registry Specification

## MODIFIED Requirements

### Requirement: Config-driven model registry
The system SHALL load all speech, LLM and diarization models from `models.toml`, shipping exactly the entries `whisper-turbo` and `whisper-large` (engine `mlx_whisper`), `gemma-cheap` and `gemma-nice` (engine `lmstudio`) and `community-1` (kind `diar`, engine `pyannote`), and SHALL contain no model identifiers in code or page JavaScript.

#### Scenario: Shipped entries load
- **GIVEN** the repository `models.toml`
- **WHEN** the registry loads
- **THEN** it exposes STT ids `whisper-turbo`, `whisper-large`, LLM ids `gemma-cheap`, `gemma-nice` and diarization id `community-1`
- **AND** `gemma-cheap` has role `cheap` and `gemma-nice` has role `nice`

#### Scenario: New model is one block
- **GIVEN** a `models.toml` with an extra `[llm.test-model]` block
- **WHEN** the registry loads
- **THEN** `test-model` is listed with no code change

#### Scenario: Unknown engine id
- **GIVEN** an STT entry with `engine = "whisperx"`, an LLM entry with `engine = "mlx_whisper"` or a diar entry with `engine = "lmstudio"`
- **WHEN** the registry loads
- **THEN** loading fails with a message naming the entry and the allowed engine ids

#### Scenario: Invalid entry named
- **GIVEN** an STT entry with an unknown `engine` or a missing `repo`
- **WHEN** the registry loads
- **THEN** loading fails with an error naming that entry

### Requirement: Models API with installed check
The system SHALL expose `GET /api/models` returning `{stt, llm, diar, lmstudio:{reachable}}` with every entry carrying `id`, `kind`, `label`, `engine`, `role`, `needs_gb`, `installed` and optional `unavailable_reason` and `hint`, accepting `?kind=stt|llm|diar` for a flat list, where STT `installed` means weights exist in the Hugging Face cache, LLM `installed` means the model key is downloaded in LM Studio, using `settings.lmstudio.url`, and diarization `installed` follows the capability `speakers-diarization`.

#### Scenario: Installed and missing
- **GIVEN** the cache has weights for `whisper-turbo` only
- **WHEN** a client requests `GET /api/models`
- **THEN** `whisper-turbo` has `installed: true` and `whisper-large` has `installed: false`

#### Scenario: Kind filter
- **GIVEN** the shipped registry
- **WHEN** a client requests `GET /api/models?kind=llm`
- **THEN** the response is a flat list of the 2 LLM entries

#### Scenario: Diarization kind filter
- **GIVEN** the shipped registry
- **WHEN** a client requests `GET /api/models?kind=diar`
- **THEN** the response is a flat list with the entry `community-1`
- **AND** an unknown kind such as `?kind=x` answers 422

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
