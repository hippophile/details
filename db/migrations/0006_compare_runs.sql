CREATE TABLE comparison (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id uuid NOT NULL REFERENCES recording (id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX comparison_recording_idx ON comparison (recording_id, created_at DESC);

CREATE TABLE comparison_item (
    comparison_id     uuid NOT NULL REFERENCES comparison (id) ON DELETE CASCADE,
    side              text NOT NULL CHECK (side IN ('A', 'B')),
    stt_run_id        uuid NOT NULL REFERENCES stt_run (id) ON DELETE CASCADE,
    analysis_group_id uuid,              -- no FK: a group is a set of analysis_run rows
    label             text NOT NULL,
    PRIMARY KEY (comparison_id, side)
);
CREATE INDEX comparison_item_run_idx ON comparison_item (stt_run_id);

CREATE TABLE eval_result (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stt_run_id    uuid NOT NULL REFERENCES stt_run (id) ON DELETE CASCADE,
    script_id     integer REFERENCES script (id) ON DELETE SET NULL,
    script_sha256 text NOT NULL,
    metrics       jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (stt_run_id, script_sha256)
);
