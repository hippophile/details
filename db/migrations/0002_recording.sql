CREATE TABLE script (
    id         integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug       text NOT NULL UNIQUE,
    name       text NOT NULL,
    body_md    text NOT NULL,
    sha256     text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE recording (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title        text NOT NULL,
    started_at   timestamptz NOT NULL,
    duration_ms  integer NOT NULL,
    audio_path   text NOT NULL,          -- relative to the data dir
    audio_sha256 text,
    container    text NOT NULL,
    codec        text NOT NULL,
    sample_rate  integer NOT NULL,
    channels     integer NOT NULL,
    channel_map  jsonb,
    source       jsonb NOT NULL DEFAULT '{}'::jsonb,
    script_id    integer REFERENCES script (id),
    notes        text,
    size_bytes   bigint,
    deleted_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX recording_started_at_idx ON recording (started_at DESC);
