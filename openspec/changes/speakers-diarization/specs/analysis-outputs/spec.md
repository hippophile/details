# analysis-outputs Specification

## MODIFIED Requirements

### Requirement: Analysis API
The system SHALL expose `POST /api/stt-runs/{id}/analyses`, `GET /api/stt-runs/{id}/analyses` and `GET /api/analyses/{group_id}`, SHALL group the per-kind runs of one request under one `group_id`, SHALL record model id, params, rendered prompt, timings and token counts per run, and SHALL accept an optional `diarization_run_id` (capability `speakers-diarization`) that is stored on each run and returned as `diarization_run_id` in the group.

#### Scenario: Start an analysis
- **GIVEN** a done STT run and an available LLM model
- **WHEN** the client POSTs `{model_id, kinds:["debrief","checklist"], output_language:"transcript"}`
- **THEN** the response is 202 with `group_id` and `job_id`
- **AND** after the job finishes, `GET /api/analyses/{group_id}` lists both outputs with timings

#### Scenario: Transcript not ready
- **GIVEN** an STT run with status `running`
- **WHEN** the client POSTs an analysis for it
- **THEN** the response is 422
- **AND** no job is enqueued

#### Scenario: Non-LLM or unknown model
- **GIVEN** a `kind=stt` model id or an id not in the registry
- **WHEN** it is used as `model_id`
- **THEN** the response is 422

#### Scenario: Job steps
- **GIVEN** a valid request
- **WHEN** the job is enqueued
- **THEN** its steps are `lm_load` with the `model_id`, `analyse`, `lm_unload` in that order

#### Scenario: Diarization id returned
- **GIVEN** a request that carries a valid `diarization_run_id`
- **WHEN** `GET /api/analyses/{group_id}` is requested
- **THEN** the group has that `diarization_run_id`
