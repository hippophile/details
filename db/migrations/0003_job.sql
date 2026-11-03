CREATE TABLE job (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id  uuid REFERENCES recording (id) ON DELETE CASCADE,
    kind          text NOT NULL,
    params        jsonb NOT NULL DEFAULT '{}'::jsonb,
    steps         jsonb NOT NULL DEFAULT '[]'::jsonb,     -- ordered [{"name", "params"}]
    status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'cancelling', 'cancelled', 'failed', 'done')),
    step          text,
    step_index    integer NOT NULL DEFAULT 0,
    step_progress real,
    message       text,
    error         text,
    attempt       integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    heartbeat_at  timestamptz
);
CREATE INDEX job_status_created_idx ON job (status, created_at);
CREATE INDEX job_recording_idx ON job (recording_id);
