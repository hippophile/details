CREATE TABLE stt_run (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id   uuid NOT NULL REFERENCES recording (id) ON DELETE CASCADE,
    job_id         uuid REFERENCES job (id) ON DELETE SET NULL,
    engine         text NOT NULL,
    model_id       text NOT NULL,
    model_revision text,
    settings       jsonb NOT NULL DEFAULT '{}'::jsonb,
    language       text,
    status         text NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    started_at     timestamptz,
    finished_at    timestamptz,
    wall_ms        integer,
    audio_ms       integer,
    rtf            real,                 -- audio seconds / wall seconds (>1 = faster than real time)
    error          text,
    text           text,
    raw            jsonb,                -- untouched engine output (before the Greek postfix)
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX stt_run_recording_idx ON stt_run (recording_id, created_at);

CREATE TABLE transcript_segment (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stt_run_id     uuid NOT NULL REFERENCES stt_run (id) ON DELETE CASCADE,
    idx            integer NOT NULL,
    start_ms       integer NOT NULL,
    end_ms         integer NOT NULL,
    text           text NOT NULL,
    avg_logprob    real,
    no_speech_prob real,
    words          jsonb,
    UNIQUE (stt_run_id, idx)
);
