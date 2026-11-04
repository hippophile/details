CREATE TABLE prompt_template (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL,
    version       integer NOT NULL,
    kind          text NOT NULL,
    body          text NOT NULL,
    output_schema jsonb NOT NULL,
    sha256        text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

-- One row per model call chain link. group_id = one Start click (what the UI calls an "analysis").
-- A retry after invalid JSON is its own row (retry_of -> the first attempt).
CREATE TABLE analysis_run (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id           uuid NOT NULL,
    stt_run_id         uuid NOT NULL REFERENCES stt_run (id) ON DELETE CASCADE,
    kind               text NOT NULL,
    prompt_template_id uuid REFERENCES prompt_template (id),
    llm_endpoint       text,
    model_id           text NOT NULL,
    params             jsonb NOT NULL DEFAULT '{}'::jsonb,
    output_language    text NOT NULL DEFAULT 'transcript',
    input_text_sha256  text,
    rendered_prompt    text,
    status             text NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    started_at         timestamptz,
    finished_at        timestamptz,
    wall_ms            integer,
    tokens_in          integer,
    tokens_out         integer,
    tok_per_s          real,
    finish_reason      text,
    raw_response       jsonb,
    schema_valid       boolean,
    validation_errors  jsonb,
    retry_of           uuid REFERENCES analysis_run (id) ON DELETE CASCADE,
    error              text,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX analysis_run_stt_idx ON analysis_run (stt_run_id, created_at);
CREATE INDEX analysis_run_group_idx ON analysis_run (group_id);

CREATE TABLE output (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id uuid NOT NULL UNIQUE REFERENCES analysis_run (id) ON DELETE CASCADE,
    kind            text NOT NULL,
    schema_version  integer NOT NULL,
    content         jsonb NOT NULL
);
