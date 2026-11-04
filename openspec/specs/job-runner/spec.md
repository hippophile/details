# job-runner Specification

## Purpose
Run long work as queued jobs of ordered steps on one GPU worker, with checkpoints, resume after restart, cancel, retry, live progress over SSE and LM Studio model load/unload.

## Requirements

### Requirement: Job table and API
The system SHALL persist jobs in a `job` table (migration 0003) with status in `queued, running, cancelling, cancelled, failed, done`, and SHALL expose `POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel` and `POST /api/jobs/{id}/retry`.

#### Scenario: Create and poll
- **GIVEN** a registered fake step `fake`
- **WHEN** a client posts a job with steps `["fake"]` and polls `GET /api/jobs/{id}`
- **THEN** the job moves `queued`, `running`, `done` and reports the current step and progress

#### Scenario: Wrong-state action
- **GIVEN** a job with status `done`
- **WHEN** a client posts cancel or retry
- **THEN** the response is 409 and the job is unchanged

#### Scenario: Unknown job
- **GIVEN** no such id
- **WHEN** a client requests `GET /api/jobs/{id}`
- **THEN** the response is 404

#### Scenario: Enqueue signature
- **GIVEN** a registered step `fake`
- **WHEN** `JobService.enqueue("test", None, ["fake", {"name": "fake", "params": {"n": 2}}], {"n": 1})` is called
- **THEN** it returns a job id and the job stores both steps in order

#### Scenario: Unknown step rejected
- **GIVEN** no step named `nope`
- **WHEN** a client posts a job with steps `["nope"]`
- **THEN** the response is 422 and no job row exists

### Requirement: Single worker with step interface
The system SHALL run at most one job at a time in FIFO order, executing each job's steps in order through a registered step interface that receives a context with a step directory, a progress callback, a cancel event and a child-process helper, and SHALL provide no concrete STT or LLM step.

#### Scenario: Strict order, one at a time
- **GIVEN** two queued jobs of two steps each with fake steps that log start and end
- **WHEN** the runner processes them
- **THEN** the log shows job 1 step 1, step 2, then job 2 step 1, step 2, with no overlap

#### Scenario: Context fields
- **GIVEN** a step registered with `@steps.register("probe")` run with job params `{"a":1,"b":1}` and step params `{"b":2}`
- **WHEN** it runs
- **THEN** its context has `job_id`, `step_name`, `cancelled`, `data_dir`, `settings`, `memory`, `progress` and `spawn_child`
- **AND** `params` is `{"a":1,"b":2}` and `step_dir` is `<data_dir>/jobs/<job_id>/probe/`

#### Scenario: Unknown step name
- **GIVEN** a job naming a step that is not registered
- **WHEN** the runner reaches it
- **THEN** the job fails with an error naming the step

### Requirement: Checkpoints and resume
The system SHALL write a `done.json` marker atomically (temporary file then rename) in the step directory under the data dir after each successful step, SHALL skip steps with that marker, and on startup SHALL return `running` and `cancelling` jobs to `queued`.

#### Scenario: Resume after restart
- **GIVEN** a job of steps A, B, C with A finished and the server stopped while B ran
- **WHEN** the runner starts again
- **THEN** the job is `queued`, then step A is not executed again, and B and C run

#### Scenario: Atomic marker
- **GIVEN** a step finishing
- **WHEN** its marker is written
- **THEN** no `.tmp` file remains and the marker parses as JSON

### Requirement: Built-in model steps
The system SHALL register built-in steps `lm_load` (param `model_id`) and `lm_unload` that call the MemoryManager, and enqueue SHALL reject an `lm_load` whose `model_id` is not in the registry with 422.

#### Scenario: Load then unload
- **GIVEN** a job with steps `["lm_load" (model_id gemma-cheap), "lm_unload"]` and a fake LM Studio
- **WHEN** the job runs
- **THEN** the fake records load of `details-gemma-cheap` then its unload, and the job is `done`

#### Scenario: Unknown model id
- **GIVEN** no registry entry `nope`
- **WHEN** a client posts a job with step `lm_load` and `model_id` `nope`
- **THEN** the response is 422

### Requirement: Terminal finalizers
The system SHALL run every `@steps.on_terminal(name)` finalizer registered for the job's steps when the job ends `failed` or `cancelled`, including a job cancelled while still queued, and a finalizer error SHALL NOT change the job's status.

#### Scenario: Failed job
- **GIVEN** a finalizer registered for step `probe` and a job whose `probe` step raises
- **WHEN** the job fails
- **THEN** the finalizer is called once with status `failed` and the error text

#### Scenario: Cancelled while queued
- **GIVEN** a queued job containing step `probe` that has never run
- **WHEN** a client cancels it
- **THEN** the finalizer for `probe` is called with status `cancelled`

#### Scenario: Finalizer raises
- **GIVEN** a finalizer that raises
- **WHEN** the job is cancelled
- **THEN** the job status is `cancelled`

### Requirement: Cascade with recordings
The `job` table SHALL reference `recording` with `ON DELETE CASCADE`.

#### Scenario: Recording deleted
- **GIVEN** a job row for a recording
- **WHEN** the recording row is deleted
- **THEN** the job row is gone

### Requirement: Failure and retry
The system SHALL mark a job `failed` with the failing step name and error when a step raises, SHALL run no later steps and SHALL NOT retry automatically, and `retry` SHALL requeue the job from the failed step keeping finished steps.

#### Scenario: Retry from failed step
- **GIVEN** a job whose step B raised once and step A is done
- **WHEN** a client posts retry and B now succeeds
- **THEN** A does not run again, B and C run, and the job ends `done`

#### Scenario: No silent retry
- **GIVEN** a step that always raises
- **WHEN** the job runs
- **THEN** the step executes exactly once and the job stays `failed`

### Requirement: Cancel
The system SHALL cancel a running job by setting `cancelling`, terminating the registered child process with SIGTERM then SIGKILL after a configured grace period, and ending in `cancelled` while keeping partial files.

#### Scenario: Cancel kills child
- **GIVEN** a step that spawns a real child process sleeping 60 s
- **WHEN** a client posts cancel
- **THEN** the child process no longer exists within the grace period plus 1 s
- **AND** the job status is `cancelled`

#### Scenario: Cancel a queued job
- **GIVEN** a queued job
- **WHEN** a client posts cancel
- **THEN** the job is `cancelled` and its steps never run

### Requirement: Heartbeat timeout
The system SHALL treat a child with no heartbeat for the configured timeout as hung, kill it and fail the job with the step name and "no heartbeat".

#### Scenario: Silent child
- **GIVEN** timeout set to 1 s and a child that prints nothing
- **WHEN** the step runs
- **THEN** the child is killed and the job is `failed` with a message containing "no heartbeat"

### Requirement: GPU lease
The system SHALL provide a lease with owners `job` and `live` in which `live` is granted before waiting jobs and a running step is never interrupted.

#### Scenario: Live waits for step, then wins
- **GIVEN** a job step holding the lease and a `live` request and a second job step both waiting
- **WHEN** the first step ends
- **THEN** `live` is granted before the second job step

### Requirement: Progress events
The system SHALL stream job progress at `GET /api/jobs/{id}/events` as server-sent events whose first event is the current state, each with an `id`, and SHALL close the stream after a terminal status.

#### Scenario: Late subscriber
- **GIVEN** a job already at step 2 of 3
- **WHEN** a client connects to the events endpoint
- **THEN** the first event reports step 2 with its current progress
- **AND** later events follow until the job is `done`, then the stream ends

#### Scenario: Reconnect
- **GIVEN** a client sends `Last-Event-ID` after a disconnect
- **WHEN** it reconnects
- **THEN** it receives the current state as its first event
