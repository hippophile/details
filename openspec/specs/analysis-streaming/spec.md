# analysis-streaming Specification

## Purpose
TBD - created by archiving change llm-streaming. Update Purpose after archive.

## Requirements

### Requirement: Streaming LM Studio client
The system SHALL provide `chat_stream` that sends `stream:true` with the same `json_schema` response format plus `stream_options.include_usage`, concatenates only `delta.content` pieces (never `reasoning_content`), calls `on_delta` for each non-empty piece and returns the same `{text, tokens_in, tokens_out, finish_reason, wall_ms, raw}` shape as the non-streaming client.

#### Scenario: Same result as a plain call
- **GIVEN** a fake LM Studio streaming a reply in 12 chunks followed by a usage chunk
- **WHEN** `chat_stream` runs
- **THEN** `text` equals the full reply, `tokens_out` equals the usage value and `finish_reason` is `stop`
- **AND** `on_delta` was called once per non-empty piece in order

#### Scenario: Reasoning ignored
- **GIVEN** chunks that carry `reasoning_content` deltas
- **WHEN** the stream is read
- **THEN** none of that text appears in `text` or in `on_delta`

#### Scenario: No usage chunk
- **GIVEN** a stream that ends with `[DONE]` and no usage
- **WHEN** it is read
- **THEN** `tokens_out` is the number of content deltas and `raw.usage_estimated` is true

### Requirement: Streaming is configurable with a safe fallback
The system SHALL read `settings.llm.stream` and `settings.llm.stream_idle_timeout_s`, SHALL fall back once to a non-streaming call when the streaming request fails before the first content delta (HTTP 400 or 422 about streaming, a non-SSE response, or no parsable chunk), and SHALL NOT fall back after the first delta.

#### Scenario: Server rejects streaming
- **GIVEN** a fake LM Studio that answers 400 "stream not supported" to `stream:true`
- **WHEN** an analysis kind is generated
- **THEN** exactly one non-streaming request follows and the result is the same as a plain call
- **AND** the call log line says `fallback`

#### Scenario: Setting off
- **GIVEN** `llm.stream = false`
- **WHEN** an analysis runs
- **THEN** no request carries `stream:true`

#### Scenario: Drop after the first delta
- **GIVEN** a stream that sends 3 deltas and then closes without a finish chunk
- **WHEN** the call is made
- **THEN** it fails with a "stream ended early" error naming LM Studio's URL
- **AND** no second request is made

#### Scenario: Silent socket
- **GIVEN** a stream that stops sending bytes for longer than the idle timeout
- **WHEN** the call is made
- **THEN** it fails with a timeout error and the connection is closed

### Requirement: Validation and quote check unchanged
The system SHALL run JSON parsing, schema validation, the single retry and the verbatim quote check on the final streamed text exactly as for a non-streamed reply, and SHALL store `rendered_prompt`, `raw_response`, tokens and `schema_valid` in `analysis_run` as before.

#### Scenario: Invalid JSON streamed
- **GIVEN** a stream whose complete text is not valid JSON, then a valid retry stream
- **WHEN** the kind is generated
- **THEN** two `analysis_run` rows exist linked by `retry_of`, like the non-streamed case
- **AND** the output holds the retry result

#### Scenario: Unverified quote
- **GIVEN** a valid streamed reply with a quote that is not in the transcript
- **WHEN** the result is stored
- **THEN** that item has `verified:false`

### Requirement: Live token stream to the page
The system SHALL expose `GET /api/jobs/{id}/stream` as server-sent events whose first event is a snapshot of the current attempt's text, followed by `attempt_start`, `delta`, `attempt_end` and `fallback` events with ids, coalesced to at most one write per 100 ms, SHALL keep only the current attempt in memory with a size cap, and SHALL close the stream when the job is terminal.

#### Scenario: Late subscriber
- **GIVEN** an attempt that has produced 200 characters
- **WHEN** a client connects
- **THEN** its first event is a snapshot holding those 200 characters
- **AND** later deltas continue from there

#### Scenario: Retry resets the box
- **GIVEN** a first attempt that ended invalid
- **WHEN** the retry begins
- **THEN** an `attempt_start` with attempt 2 is sent and new deltas start from empty text

#### Scenario: Unknown job and finished job
- **GIVEN** an unknown job id, and separately a job that is already done
- **WHEN** each is requested
- **THEN** the first answer is 404 and the second is a snapshot with empty text and a closed stream

### Requirement: Cancel mid-stream
The system SHALL close the LM Studio connection within one second of a cancel request while a reply is streaming, SHALL store no output and no partial text for that attempt, and SHALL end the job `cancelled` with the existing finalizer behaviour.

#### Scenario: Cancel during generation
- **GIVEN** a slow fake stream that has sent 3 deltas
- **WHEN** the job is cancelled
- **THEN** the fake observes the connection closed in under 1 s
- **AND** the job is `cancelled`, no `output` row exists and every `analysis_run` row is `cancelled`, none `failed`

### Requirement: Streaming never leaks content or secrets into logs
The system MUST NOT write delta text or prompt text to the job log, MUST redact credentials in streaming errors, and SHALL add `stream`, `ttft_ms` and `fallback` to the LM call log line.

#### Scenario: Log contents
- **GIVEN** a streamed analysis
- **WHEN** the job log is read
- **THEN** the call line has `stream=true` and `ttft_ms`
- **AND** no word of the reply or prompt appears in any line

#### Scenario: Secret in a stream error chunk
- **GIVEN** a fake stream that sends an error chunk containing `password=SeCr3tPw`
- **WHEN** the call fails
- **THEN** `SeCr3tPw` is absent from the log, the hub snapshot, the job error and `GET /api/analyses/{group_id}` `.error`

### Requirement: Live writing box on the Analyse page
The Analyse page SHALL show, while the analyse step runs, a box labelled as an unchecked draft that displays the streamed raw text as it arrives, the kind, attempt number, chunk position in map-reduce, a retrying marker, token count and tok/s computed in the browser, SHALL clear the box when the kind finishes, and SHALL set all text with `textContent`.

#### Scenario: Watching a run
- **GIVEN** a running analysis with streaming on
- **WHEN** deltas arrive
- **THEN** the box grows and auto-scrolls until the user scrolls up
- **AND** when the kind finishes the box is cleared and the validated result appears as before

#### Scenario: Stream unavailable
- **GIVEN** the `/stream` connection fails
- **WHEN** the analysis continues
- **THEN** the box shows "live view unavailable" and the normal progress bar and final result still work
