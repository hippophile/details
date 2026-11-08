CREATE TABLE diarization_run (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id uuid NOT NULL REFERENCES recording (id) ON DELETE CASCADE,
    job_id       uuid REFERENCES job (id) ON DELETE SET NULL,
    engine       text NOT NULL,                       -- registry engine id, e.g. pyannote
    model_id     text NOT NULL,                       -- models.toml [diar.<id>]
    settings     jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {min_speakers, max_speakers, device, merge_gap_ms}
    status       text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    started_at   timestamptz,
    finished_at  timestamptz,
    wall_ms      integer,
    audio_ms     integer,                             -- recording.duration_ms read at step start (same source as stt_run.audio_ms)
    rtf          real,                                -- audio seconds / wall seconds
    speakers     jsonb,                               -- [{speaker, talk_ms, share_pct, turns}], computed by code at completion
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX diarization_run_recording_idx ON diarization_run (recording_id, created_at);

CREATE TABLE diar_turn (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    diarization_run_id uuid NOT NULL REFERENCES diarization_run (id) ON DELETE CASCADE,
    idx                integer NOT NULL,
    start_ms           integer NOT NULL,
    end_ms             integer NOT NULL,
    speaker            integer NOT NULL CHECK (speaker >= 1),   -- 1..n by first appearance
    CHECK (end_ms > start_ms),
    UNIQUE (diarization_run_id, idx)
);
CREATE INDEX diar_turn_run_start_idx ON diar_turn (diarization_run_id, start_ms);

CREATE TABLE speaker_name (
    diarization_run_id uuid NOT NULL REFERENCES diarization_run (id) ON DELETE CASCADE,
    speaker            integer NOT NULL CHECK (speaker >= 1),
    name               text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 60),
    PRIMARY KEY (diarization_run_id, speaker)
);

ALTER TABLE analysis_run
    ADD COLUMN diarization_run_id uuid REFERENCES diarization_run (id) ON DELETE SET NULL;
