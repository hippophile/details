# stt-runs Specification

## MODIFIED Requirements

### Requirement: STT run API
The system SHALL expose `POST /api/recordings/{id}/stt-runs`, `GET /api/recordings/{id}/stt-runs`, `GET /api/stt-runs/{id}`, `GET /api/stt-runs/{id}/segments` and `DELETE /api/stt-runs/{id}`, where POST validates the model, inserts a `queued` row, enqueues the job and returns 202 with `stt_run_id` and `job_id`, and where `GET /api/stt-runs/{id}/segments` accepts an optional `diarization_run_id` that adds speaker fields as defined in the capability `speakers-diarization`.

#### Scenario: Start a run
- **GIVEN** a recording and an available STT model
- **WHEN** the client POSTs `{model_id, language:"el", vocab_hint:true}`
- **THEN** the response is 202 with `stt_run_id` and `job_id`
- **AND** the run appears in the list with status `queued`

#### Scenario: LLM model refused
- **GIVEN** a registry entry with `kind="llm"`
- **WHEN** the client POSTs it as `model_id`
- **THEN** the response is 422
- **AND** no job is enqueued

#### Scenario: Unavailable model refused
- **GIVEN** a model whose `installed` is false
- **WHEN** the client POSTs it
- **THEN** the response is 409 with the reason

#### Scenario: Delete a running or queued run
- **GIVEN** a run with status `queued` or `running`
- **WHEN** the client sends DELETE
- **THEN** the response is 409
- **AND** the run still exists

#### Scenario: Segments without the parameter are unchanged
- **GIVEN** a done run with segments
- **WHEN** the client requests `GET /api/stt-runs/{id}/segments`
- **THEN** each row has exactly the keys `idx, start_ms, end_ms, text, avg_logprob, no_speech_prob`
