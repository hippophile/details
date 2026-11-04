-- prompt-ab: code checks that belong to a prompt version (front matter `checks: a, b`). Immutable with the version.
-- Existing rows get {} so v1 behaves exactly as before.
ALTER TABLE prompt_template ADD COLUMN checks text[] NOT NULL DEFAULT '{}';
