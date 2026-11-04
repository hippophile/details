## ADDED Requirements

### Requirement: Step records
The system SHALL write `<step_dir>/step.json` atomically when a step begins and when it ends, containing index, name, status, started_at, finished_at, duration_ms, error, model, tokens_in, tokens_out, tok_per_s and free RAM at start and end, SHALL let a step add `model` and token fields through `ctx.metrics`, and SHALL expose them at `GET /api/jobs/{id}/steps` with `pending` for steps not yet reached.

#### Scenario: Metrics from a step
- **GIVEN** a job whose step calls `ctx.metrics(model="gemma-cheap", tokens_in=100, tokens_out=50)`
- **WHEN** the step finishes and a client calls `GET /api/jobs/{id}/steps`
- **THEN** that step shows status `done`, a duration, the model and the token counts
- **AND** later steps of the job show `pending`

#### Scenario: Resumed job keeps finished steps
- **GIVEN** a failed job retried from its second step
- **WHEN** the steps are read
- **THEN** the first step still shows `done` with its original duration

#### Scenario: Waiting for the GPU
- **GIVEN** a step waiting for the lease
- **WHEN** the steps are read
- **THEN** it shows `waiting_gpu` and its `started_at` is set only when the lease is granted

#### Scenario: Interrupted
- **GIVEN** a `step.json` with status `running` for a job that is `queued` after a restart
- **WHEN** the steps are read
- **THEN** that step is reported as `interrupted`

### Requirement: Steps in the progress events
The system SHALL add a `steps` list to every state in `GET /api/jobs/{id}/events` without changing any existing field, and SHALL emit a new event when a step record changes.

#### Scenario: Live metrics
- **GIVEN** a client connected to the events of a running job
- **WHEN** a step reports new token counts
- **THEN** a new event arrives whose `steps` shows them
- **AND** `status`, `step`, `pct` and `msg` keep their previous meaning

### Requirement: Structured job log
The system SHALL append JSON log lines with a per-job increasing `n`, `ts`, `level`, `step`, `src` and `msg` to `<step_dir>/log.jsonl` (job-level lines to `<data_dir>/jobs/<id>/_job/log.jsonl`), SHALL continue `n` after a restart, and SHALL write one truncation line and then stop at `settings.runner.log_max_lines`.

#### Scenario: Ordered across steps
- **GIVEN** a job with three steps that each log twice
- **WHEN** the log is read
- **THEN** the lines come back ordered by `n` with the right `step` on each

#### Scenario: Restart continues numbering
- **GIVEN** a job log whose highest `n` is 7 and a restarted server
- **WHEN** the job logs another line
- **THEN** its `n` is 8

#### Scenario: Cap
- **GIVEN** `log_max_lines=5`
- **WHEN** a step logs 20 lines
- **THEN** the log holds 5 lines plus one `log truncated` line, in total no more than 6 lines

### Requirement: What the log records
The system SHALL log step begin and end with duration, cancel and failure, model load and unload requests with the identifiers loaded afterwards, every child process stdout line as a worker line, the last 20 lines of `child.stderr.log` when a child fails, and one line per LM Studio call with model, kind, attempt, token counts, wall time, finish reason and status, and SHALL NOT log prompt, reply or transcript text.

#### Scenario: LM Studio call line
- **GIVEN** an analyse job against a fake LM Studio
- **WHEN** the job is done
- **THEN** the log has one `src=llm` line per call with tokens and `status=ok`
- **AND** no line contains any word of the prompt or the reply

#### Scenario: Child failure shows stderr tail
- **GIVEN** a child process that prints 30 stderr lines and exits non-zero
- **WHEN** the job fails
- **THEN** the log contains the last 20 of those lines and the job error

### Requirement: No secrets in logs
The system MUST redact URL credentials, `password=`/`token=`/`api_key=` values, bearer tokens and the configured database URLs from every log line, step record and job error before they are written and again before they are served.

#### Scenario: Password in an exception
- **GIVEN** a step that raises an error containing `postgresql://u:SeCr3tPw@db/x`
- **WHEN** the job fails
- **THEN** `SeCr3tPw` appears in none of `log.jsonl`, `GET /api/jobs/{id}/log`, the download, `step.json` or the job's `error`
- **AND** the line still shows the host and that a connection failed

#### Scenario: Secret reaches the analysis error
- **GIVEN** the LM Studio 500 body containing `password=SeCr3tPw`
- **WHEN** the step fails and `GET /api/analyses/{group_id}` is read
- **THEN** its `error` field does not contain the secret

#### Scenario: Echoed secret in an LM Studio error body
- **GIVEN** a fake LM Studio that answers 500 with a body containing `password=SeCr3tPw`
- **WHEN** the analyse step fails
- **THEN** the secret appears nowhere in the log, steps or job error

### Requirement: Log API and download
The system SHALL serve `GET /api/jobs/{id}/log?after=<n>&limit=<=1000>` returning `{lines, next, done}`, SHALL serve the whole log as `text/plain` with a `Content-Disposition` filename for `?download=1`, and SHALL answer 404 for an unknown job.

#### Scenario: Incremental read
- **GIVEN** a job log with lines 1 to 10
- **WHEN** a client asks `after=6`
- **THEN** it receives lines 7 to 10 and `next=10`
- **AND** `done` is true only when the job is terminal

#### Scenario: Download
- **GIVEN** a finished job
- **WHEN** a client asks `?download=1`
- **THEN** it receives readable text lines and an attachment filename containing the job id prefix

#### Scenario: Unknown job
- **GIVEN** no such job
- **WHEN** the log is requested
- **THEN** the answer is 404

### Requirement: Pipeline and log panels
The Analyse page SHALL show, in the Running card, a table of the job's steps with status, live duration, model, tokens in and out, tok/s and free RAM from the SSE `steps`, and a collapsible log panel that tails `GET /api/jobs/{id}/log` every second, filters by level, pauses auto-scroll when the user scrolls up and offers the download, and SHALL set all text with `textContent`.

#### Scenario: Live view
- **GIVEN** a running three-step job
- **WHEN** the page receives step updates
- **THEN** the finished step shows its duration and the running step's duration keeps ticking

#### Scenario: Log after the job ended
- **GIVEN** a finished analysis with a `job_id`
- **WHEN** the user opens its log from the analysis footer
- **THEN** the panel shows the stored lines and no polling continues

### Requirement: Prompt inspector API
The system SHALL serve `GET /api/analysis-runs/{run_id}/inspect` returning the retry chain with, per attempt, the stored rendered prompt, raw reply text, token counts, tok/s, wall time, finish reason, validation result and error, plus kind, model, template name, version and sha256, output language and params, with credentials redacted, and SHALL mark map-reduce runs with `prompt_scope="first_chunk"` and `raw_text_kind="merged_by_code"`.

#### Scenario: Retried run
- **GIVEN** a kind whose first reply was invalid JSON and whose retry was valid
- **WHEN** the inspector is requested with the run id the analyses API returned
- **THEN** both attempts are returned in order with their own prompts and replies

#### Scenario: Map-reduce honesty
- **GIVEN** an analysis run with `params.mode = "map-reduce"`
- **WHEN** the inspector is requested
- **THEN** `prompt_scope` is `first_chunk` and `raw_text_kind` is `merged_by_code`

#### Scenario: Call failed before a reply
- **GIVEN** a run whose LM Studio call failed
- **WHEN** the inspector is requested
- **THEN** `rendered_prompt` is null and the error text is present

#### Scenario: Unknown run
- **GIVEN** a random UUID
- **WHEN** the inspector is requested
- **THEN** the answer is 404

### Requirement: Prompt inspector page
Each output on the Analyse page SHALL have an Inspect control that opens a dialog with Prompt, Raw reply and Params tabs, a copy button and an attempt switcher when a retry exists, with all text set through `textContent`.

#### Scenario: Open and copy
- **GIVEN** a finished analysis with a checklist output
- **WHEN** the user opens Inspect and the Prompt tab
- **THEN** the exact stored prompt is shown
- **AND** Copy places that text on the clipboard

### Requirement: Unload app-loaded model on terminal failure or cancel
The `analyse` finalizer SHALL, when the job ended `failed` or `cancelled` and `settings.lmstudio.unload_on_terminal` is true, unload the models this app loaded, SHALL NOT unload models it did not load unless `lmstudio.exclusive` is set, SHALL skip the unload while another job is `running` or `cancelling` or the lease owner is `live`, and an unload error SHALL be logged without changing the job status.

#### Scenario: Failed job releases memory
- **GIVEN** an analyse job that failed after `lm_load` loaded `details-gemma-cheap` while the user's own model was also loaded
- **WHEN** the finalizer runs
- **THEN** only `details-gemma-cheap` is unloaded

#### Scenario: Cancel of a queued job during another run
- **GIVEN** job A running with its model loaded and queued analyse job B
- **WHEN** B is cancelled
- **THEN** no unload happens and A keeps its model

#### Scenario: Live assistant holds the lease
- **GIVEN** the lease owner is `live` and a queued analyse job is cancelled
- **WHEN** its finalizer runs directly from the cancel call
- **THEN** no unload happens

#### Scenario: Unload fails
- **GIVEN** LM Studio unreachable when the finalizer runs
- **WHEN** the finalizer runs
- **THEN** the job stays `failed` and the log has a warning line

### Requirement: Retry reloads the model
The `analyse` step SHALL ensure its model is loaded (a no-op when it already is) before its first call, so that retrying a failed job whose `lm_load` step was skipped still works after a terminal unload.

#### Scenario: Retry after failure
- **GIVEN** a failed analyse job whose model was unloaded by the finalizer
- **WHEN** the job is retried and LM Studio answers
- **THEN** the model is loaded again, the analysis completes and `lm_unload` runs last

#### Scenario: Already loaded
- **GIVEN** the model is loaded
- **WHEN** the analyse step starts
- **THEN** no load call is made
