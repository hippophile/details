# detAIls v2 — Data & contracts architecture

## 1. TLDR
- **Hybrid.** Audio = files on disk, outside the repo. Everything else (metadata, transcripts, runs, outputs, prompts) = **PostgreSQL**, with big/raw payloads as `jsonb`.
- **Immutable runs.** Every STT run and every LLM run is a new row. Nothing is overwritten. Comparison = join two rows.
- **LLM outputs = JSON validated against versioned JSON Schemas.** Markdown is *rendered from* the JSON, never parsed back.
- **Prompt templates = files in git** (`prompts/<name>/vN.md`), copied *as full text* into the run row (hash + body). A run is reproducible even if the file changes.
- **Migrations = numbered plain `.sql` files + ~30-line Python runner.** No ORM. Import v1 `sessions/` with a one-off CLI.

## 2. Decisions

### 2.1 Storage: hybrid (audio on disk, rest in Postgres)
| Need | Files only | Postgres only | **Hybrid** |
|---|---|---|---|
| Compare run A vs B, filter by model/tag | hard (scan JSON) | easy (SQL) | easy |
| Audio blobs (WAV ≈ 1.9 MB/min, 16 kHz mono 16-bit; measured: 384 KB file in `sessions/`) | natural | bad (bloats DB, backups) | natural |
| Checklist done-state updates | rewrite file | one `UPDATE` | one `UPDATE` |
| Delete a recording fully | `rm` | `DELETE` cascade | `DELETE` + `rm` (one function) |
| Setup cost | none | container exists already | container exists already |

Reason: the compare use case is *queries* (which model got most tech words, how long, how many tokens). Postgres does that well and already runs. Audio stays files because DB blobs give no benefit locally.

Safety net: every run also gets `data/recordings/<id>/runs/<run_id>.json` export? **No.** Keep one source of truth. Instead: `make backup` = `pg_dump` into the data dir. (Caveat: GitHub is not a backup for recordings, they never go in git. Backup is the user's job: see Risks.)

Connection: `DATABASE_URL` env var (psycopg 3). Dedicated database `details`, not the default one.

### 2.2 Where files live
```
$DETAILS_DATA_DIR            # env var, default ~/Library/Application Support/detAIls
  recordings/<recording_id>/
      audio.<ext>            # original as captured (immutable)
      audio.16k.wav          # normalized copy for STT (derived, can be rebuilt)
  exports/<run_id>.md        # optional, regenerated on demand
  backups/*.sql.gz
```
- Path in DB is **relative** to `DETAILS_DATA_DIR`. Moving the folder = change one env var.
- `recording_id` = UUID. Not the timestamp (two recordings in one second, and no personal info in names).
- `sha256` of `audio.<ext>` stored. Detects corruption + duplicate imports.
- Repo `.gitignore` already has `sessions/`, `*.wav`, `*.aiff`, `*.m4a`. **Add** `*.webm`, `*.opus`, `*.flac`, `*.mp3`, `*.caf`, `data/`. Default data dir is outside the repo, so git cannot see it anyway.

### 2.3 Data model (Postgres)
Rules: `id uuid default gen_random_uuid()` (built in, PG13+), `created_at timestamptz default now()`, no `UPDATE` on result rows (except `checklist_item.done` and `tag`/title edits).

```
script            (id, name, body_md, sha256, created_at)           -- test scripts shown on record page
recording         (id, title, started_at, duration_ms, audio_path, audio_sha256, container, codec,
                   sample_rate, channels, channel_map jsonb, source jsonb, script_id?, notes, deleted_at?)
stt_run           (id, recording_id, engine, model_id, model_revision, settings jsonb, language,
                   status, started_at, finished_at, wall_ms, rtf, peak_ram_mb?, error?,
                   text, raw jsonb)              -- raw = engine output untouched
transcript_segment(id, stt_run_id, idx, start_ms, end_ms, text, speaker?, avg_logprob?, no_speech_prob?, words jsonb)
diarization_run   (id, recording_id, engine, model_id, settings jsonb, status, wall_ms, speakers jsonb, error?)
diar_turn         (id, diarization_run_id, start_ms, end_ms, speaker)
prompt_template   (id, name, version int, body, output_schema text, sha256, created_at, unique(name,version))
analysis_run      (id, stt_run_id, diarization_run_id?, prompt_template_id, llm_endpoint, model_id, params jsonb,
                   input_text_sha256, rendered_prompt text, status, started_at, finished_at, wall_ms,
                   tokens_in, tokens_out, tok_per_s, finish_reason, raw_response jsonb, schema_valid bool,
                   validation_errors jsonb, retry_of?)
output            (id, analysis_run_id, kind, schema_version, content jsonb)   -- one row per kind
checklist_item    (id, output_id, idx, text, owner?, due?, done bool, done_at?)  -- projection of action_items/checklist
tag               (id, name unique)  /  recording_tag (recording_id, tag_id, source 'llm'|'user', analysis_run_id?)
comparison        (id, name, recording_id, created_at)
comparison_item   (comparison_id, run_kind 'stt'|'analysis', run_id, label)
eval_result       (id, stt_run_id, reference_id?, metrics jsonb)    -- WER, tech-term recall etc. (from eval/score.py)
```
Notes:
- **Chain:** `recording → stt_run → analysis_run → output`. Changing STT model = new `stt_run`; changing LLM = new `analysis_run` on the same `stt_run`. Both are comparable by `recording_id`.
- `stt_run.settings` holds: language, `initial_prompt`/vocab hint, temperature, VAD, `condition_on_previous_text` — exactly the knobs in v1 `transcribe()`.
- `raw` / `raw_response` keep engine output as-is, so a parser bug never loses data. (Size: text only, small. Estimate, not measured: <1 MB per run.)
- `checklist_item.done` is the only mutable result. Re-running analysis creates a *new* run with fresh unchecked items. Old ticks stay with the old run. UI should show "latest run" by default and warn on that.
- `output.content` is the validated JSON (source of truth). `checklist_item` is a derived table so ticking is a cheap `UPDATE`; on tick, the server also patches `content.items[i].done` in the same transaction. (Alternative: no projection, patch jsonb only. Simpler, slightly uglier SQL. Fine either way; slice 3 decides.)
- `deleted_at` = soft delete (trash) for 30 days (configurable), then hard purge (see 2.7).
- `speakers` on a recording: diarization gives `SPEAKER_00…`; user-assigned names live in `speaker_name(recording_id, label, name)`; **renaming never edits transcripts**, it is applied at render time.
- Timestamps in **integer milliseconds** everywhere. No floats for time.
- Useful views: `v_run_compare` (per recording: stt model, wall_ms, rtf, wer, tech recall), `v_analysis_compare` (model, tok/s, tokens, schema_valid, wall_ms).

### 2.4 Versioning: why re-runs never overwrite
- Runs are append-only; `(recording_id, model_id, settings hash)` is **not** unique. Running the same thing twice gives two rows (LLMs and Whisper at temp>0 are not deterministic; the user may want to see variance).
- `prompt_template` rows are append-only; edit = new `version`.
- `analysis_run.retry_of` links a retry after schema failure to the failed run. Failures are stored, not hidden.
- Recording audio is immutable. "Trim" or "normalize" makes a *derived* file, never edits the original.

### 2.5 Output contracts (JSON Schema, draft 2020-12)
Files live in git: `schemas/<kind>.v1.json`. One validator (`jsonschema` lib). The LLM call asks for JSON (LM Studio supports `response_format: {type:"json_schema", json_schema:{...}}` on the OpenAI-compatible endpoint: https://lmstudio.ai/docs/app/api/structured-output). Server validates again anyway. If invalid: 1 automatic retry with the error text, then store `schema_valid=false` and show raw output. **Never silently repair.**

**Design choice: one call per `kind`, not one giant JSON.** Small models (Gemma E4B) fail more on big schemas (estimate, not measured). Each kind is its own `analysis_run` unit and its own `output` row, so you can compare models per kind. The "full analysis" button = a list of kinds run in sequence (job runner owns that).

Kinds and shapes (all have `"schema_version": 1`; all text fields strings, may be Greek or English; **language of output = language of transcript unless the template says otherwise**):

| kind | shape (required fields in bold) |
|---|---|
| `debrief` | **summary** (string, ≤1200 chars), **decisions[]** `{text, evidence_ms?}`, **action_items[]** `{text, owner?, due?, done=false}`, **risks[]** `{text, severity: low\|med\|high}`, **open_questions[]** (string), **tags[]** (string) |
| `notes` | **title**, **sections[]** `{heading, bullets[]}` |
| `lists` | **lists[]** `{title, items[]}` (e.g. "tools mentioned", "numbers & dates") |
| `checklist` | **title**, **items[]** `{text, owner?, due?, done=false}` |
| `diagram` | **title**, **type** `flowchart\|sequence\|mindmap\|timeline`, **mermaid** (string) |
| `speakers` | **speakers[]** `{label, name?, talk_ms, share_pct, turns}` — computed from `diar_turn`, **not** by the LLM |
| `tags` | **tags[]** (lowercase, kebab-case, ≤8) |

Rules:
- `due`: ISO date `YYYY-MM-DD` or null. Never free text like "next week" (put that in `text`).
- `evidence_ms` = start of the segment backing the claim. Lets the UI jump to the audio. Optional, because small models invent it (verify: must exist in the transcript, else dropped by validator code, flagged).
- `speakers` is **calculated** from diarization data. Reason: LLMs cannot count talk time. Same for any number: code computes, LLM writes prose.
- **Mermaid is plain text.** The schema checks only non-empty + starts with a known keyword (`flowchart`, `sequenceDiagram`, `mindmap`, `timeline`) + max length. Rendering/syntax check = page side (mermaid.js `parse`). If render fails: show the source text, mark `diagram_render_ok=false` in the UI only (not DB). Mermaid.js is local (vendored file), no CDN, to keep it 100% offline.
- `tags` merge: LLM tags go to `recording_tag` with `source='llm'`; user can add/remove `source='user'`.
- Evolution: breaking change = `kind.v2.json`; `output.schema_version` says which. Old rows stay valid under v1. Renderers keep v1 support.

### 2.6 Markdown export format
One file per analysis (or per set of kinds), rendered from JSON by code. Deterministic. Front matter records provenance:
```markdown
---
recording: 7f3a…          # uuid
title: Team sync 3 Nov
recorded_at: 2026-11-03T21:43:29+02:00
duration: 00:01:17
stt: mlx-community/whisper-large-v3-mlx (run 12)
llm: gemma-4-26b-a4b-qat (run 31), template debrief@3
tags: [ai-adoption, agents]
---
# Debrief
## Summary
…
## Decisions
- …
## Action items
- [ ] Prepare PoC — owner: Nikos — due: 2026-11-15
- [x] …
## Risks
- **high** …
## Diagram
```mermaid
flowchart LR …
```
## Speakers
| Speaker | Talk time | Share |
## Transcript   (optional)
**[00:00:03] Speaker A:** …
```
- `- [ ]` / `- [x]` = GitHub-style checkboxes (work in Obsidian, GitHub, VS Code).
- Export includes the done-state at export time. Re-import is **not** supported (one way).
- Filename: `<date>-<slugified title>-run<id>.md`. Written to `exports/` or downloaded by browser.
- Also offer raw `.json` download (the `output.content`). Cheap, lossless.

### 2.7 Prompt templates as data
- **Source of truth for authoring: git files** `prompts/<name>/v<N>.md` (+ matching `schemas/<kind>.v1.json`). Reason: diffable, reviewable, OpenSpec-friendly, the user edits in his editor.
- Format: Markdown body with `{{transcript}}`, `{{speakers}}`, `{{language}}`, `{{recording_title}}`, `{{script}}` placeholders. Simple string replace, no Jinja (no logic in templates; fewer surprises).
- Front matter: `name, version, kind, output_schema, description, suggested_params`.
- **Sync at startup** (or on `make prompts`): for each file compute sha256; if `(name,version)` is missing → insert into `prompt_template`; if exists with a *different* sha → **error: "edit = new version file"**. Never mutate an old version.
- A run records: `prompt_template_id` (+ via it the hash), the **fully rendered prompt** (`rendered_prompt`), `model_id`, `params` (temperature, top_p, max_tokens, `reasoning_effort`, context length), endpoint. This is enough to replay the exact request.
- Model identity: LM Studio model id string as reported by `/v1/models`, plus quantization if reported (`/api/v0/models`). Store what the server returned, don't normalize. (Source: LM Studio REST docs https://lmstudio.ai/docs/app/api/endpoints/rest — verify field names at build time.)
- No UI template editor in v2.0. Edit files, restart/sync. (Slice 8 optional.)

### 2.8 Retention & privacy
- Audio only in `DETAILS_DATA_DIR`. Default is under `~/Library/Application Support`, not under `~/code`. Nothing there is tracked by git. Postgres data lives in the OrbStack volume, also outside repo.
- **No telemetry, no cloud.** LLM endpoint is `localhost` only: config validator refuses non-loopback hosts unless `ALLOW_REMOTE_LLM=1` (explicit opt-in).
- Delete = one function `delete_recording(id)`: `DELETE FROM recording` (cascades to all runs/outputs) + remove folder + secure-ish `rm` (APFS has no reliable secure delete; FileVault is the real protection — **turn on FileVault**, it's already the user's practice).
- Soft delete 30 days (env `TRASH_DAYS`), then purge job. "Delete now" button skips trash.
- `rendered_prompt` and `raw_response` contain full transcript text = personal data. They are deleted by the same cascade. `pg_dump` backups contain them too: put backups inside the data dir and keep them 7 days.
- Test fixtures: only synthetic/scripted audio (the test-script reading) may be committed, and only under `tests/fixtures/`, small. Real meetings never.
- Git guard: a pre-commit check (tiny script) fails if a staged file has an audio extension outside `tests/fixtures/`. (Small slice.)

### 2.9 Migrations
Choice: **numbered plain SQL files** `db/migrations/0001_init.sql`, a `schema_migration(version, applied_at, sha256)` table, and a ~30-line runner (`python -m details.db migrate`, also `make db`). Each file runs in one transaction. Forward-only; fix mistakes with a new file.
Why: the user is a solo .NET dev; this is the same idea as EF migrations but with no ORM magic. Alembic needs SQLAlchemy models (extra layer for ~15 tables). Data access = `psycopg` 3 + hand-written SQL.
- Tests (tests-first): run migrations on a throwaway DB `details_test` created per test session (`CREATE DATABASE … TEMPLATE`); assert tables, constraints, cascade delete.
- Alternative if the user prefers a tool: `dbmate` (single binary, same plain-SQL files, `brew install dbmate`). The SQL files are compatible, so this is a reversible choice.
- Needs: `CREATE EXTENSION`: none (use built-in `gen_random_uuid()`). No pgvector in v2.0.

### 2.10 Import of v1 `sessions/`
v1 has `sessions/<YYYYMMDD-HHMMSS>.wav` + `.txt` (16 kHz mono int16 WAV; txt = the full-pass transcript of whichever Whisper model was selected; **model name and settings were NOT saved**; `sessions/out/*.results.json` from `score.py` may exist).
CLI: `python -m details.import_v1 [--dir sessions] [--dry-run]`
1. For each `.wav`: sha256; skip if already in `recording.audio_sha256` (idempotent, safe to re-run).
2. Copy (not move) into `recordings/<uuid>/audio.wav`; `started_at` parsed from filename (local time), `duration_ms` from WAV header, `source = {"import":"v1"}`, `container=wav, codec=pcm_s16le`.
3. If `.txt` exists: create one `stt_run` with `engine='mlx-whisper'`, `model_id='unknown (v1 import)'`, `settings={}`, `text` from the file, **no segments** (v1 stored none). Marked `source: v1`.
4. If `sessions/out/<stem>.<model>.txt` exist (from `score.py`): one `stt_run` each with a known model (`turbo`→`mlx-community/whisper-large-v3-turbo`, `large`→`…-whisper-large-v3-mlx`, `+prompt` → `settings.initial_prompt` = `PROMPT`), plus `eval_result` from `results.json`.
5. Print a table: imported / skipped / failed. Original `sessions/` is left untouched; user deletes it himself after checking.
Caveat: the dry-run is required first (tests-first: fixture = the existing 1 sample).

## 3. Rejected options
| Option | Why not |
|---|---|
| Files only (JSON next to audio) | Compare/filter/checklist updates become custom code; two sources of truth when files and index drift. Would be OK for 5 recordings, not for model comparison over many. |
| Everything in Postgres incl. audio (`bytea`/large objects) | Bloats DB and dumps; no benefit locally; streaming audio to `<audio>` needs a file/range handler anyway. |
| SQLite | Forbidden by brief. |
| Alembic / SQLAlchemy | Extra layer, model classes for 15 tables, for one dev. Plain SQL is clearer. |
| One giant JSON from the LLM | More schema failures on small models; can't compare per kind. |
| Parse Markdown back from LLM | Fragile. JSON in, Markdown out. |
| Prompt templates only in DB | No diff/review, edit friction. DB holds an immutable copy only. |
| Jinja templates | Logic in prompts hides what the model saw; `rendered_prompt` stored anyway, but keep it simple. |
| Store LLM-computed talk time | LLMs miscount. Compute from diarization. |
| Hash-dedupe of identical runs | User may want variance; storage is cheap. |

## 4. Risks and caveats (decision-changing only)
- **Diarization data may not exist on day 1.** Speakers kind needs a diarization engine on MLX/Apple Silicon (job/STT architects decide). If absent, `speakers` is hidden and `transcript_segment.speaker` is null. Schema already allows this.
- **Backup is on the user.** Recordings are not in git (rule), Postgres is a Docker volume. If the OrbStack volume or Mac is lost, everything is gone. Recommend: Time Machine to an external disk *for the data dir only* or periodic copy. Note: the user previously said "no Time Machine"; so offer `make backup` to an external folder and **ask** (Q2).
- **Data-dir default** under `~/Library/Application Support` is hard for a Mac newcomer to find. Provide a "Reveal in Finder" command and show the path on the Settings area.
- **Word-level timestamps** differ per engine (Whisper has them with `word_timestamps=True`, slower; Parakeet/Canary differ). `words jsonb` is nullable. UI must not depend on it.
- **Structured output support** in LM Studio depends on model/runtime; fall back to "JSON in prompt + validate + retry". Test per model (this is itself a comparison metric: `schema_valid` rate).
- **Concurrent writes:** one local user, but job runner writes while pages read. Postgres handles it; keep each run's insert in a single transaction so pages never see half a run.
- **Time zones:** store `timestamptz`; display local. Imported v1 names are local time without zone (assume Europe/Athens, from `DETAILS_TZ` env).
- Size/time numbers above are **estimates, not measured**.

## 5. Open questions (max 5)
1. **Backup:** OK to have `make backup` (pg_dump + copy audio to an external folder you choose)? Or accept the risk?
2. **Retention:** default trash 30 days then purge — OK? Or keep forever until you delete manually?
3. **Original audio format:** I assume original is kept as captured and a 16 kHz WAV copy is derived (decision belongs to the Record architect). Is keeping two copies (≈ +1.9 MB/min for the WAV) fine?
4. **Names:** should speaker names be remembered across recordings (a small `person` table, voice → name later) or only per recording? I recommend per recording for v2.0.
5. **Language of outputs:** write the debrief in the transcript language (Greek) by default, or always English? Affects the templates, so decide before slice 5.

## 6. What I need from the other architects
| From | Need |
|---|---|
| Job runner | Calls `create_run(...)` → row `status=queued→running→done/failed`; writes `wall_ms`, `peak_ram_mb`, `error`. Cancel = `status='cancelled'`. One run per row, no in-place retry. Sequential model load (memory budget) is theirs. |
| Job runner | Stable `engine` + `model_id` strings and a config-driven model list (env/file), not hardcoded. |
| Record/audio | Which container/codec is saved; send `duration_ms`, `sample_rate`, `channels` and, for multichannel (mic+system tap), a `channel_map` e.g. `{"0":"mic","1":"system"}`. A tap gives free "who" separation (me vs them) — useful for diarization. |
| Pages | Read-only SQL views/queries I define; pages never write result rows directly except checklist tick, tags, title, speaker names. Mermaid rendered client-side from vendored JS. |
| STT/LLM architects | Engines return segments as `{start_ms,end_ms,text,words?,avg_logprob?}`; LLM client returns `{text, tokens_in, tokens_out, finish_reason, wall_ms, raw}`. |

## 7. Proposed OpenSpec change slices
1. `data-config-and-dirs` — env config (`DATABASE_URL`, `DETAILS_DATA_DIR`, `TRASH_DAYS`), data dir layout, `.gitignore` additions, git audio guard.
2. `db-migrations-runner` — `db/migrations/*.sql` runner + `schema_migration` table + test DB fixture.
3. `schema-recordings` — `script`, `recording`, `speaker_name`, `tag` tables; file store (save/open/sha256).
4. `schema-stt-runs` — `stt_run`, `transcript_segment`, `eval_result`; append-only run API.
5. `import-v1-sessions` — idempotent importer with dry-run (+ `score.py` outputs).
6. `schema-diarization` — `diarization_run`, `diar_turn`, talk-time calculation.
7. `prompt-templates` — `prompts/` files, front matter, sync + immutability check, `prompt_template` table.
8. `output-schemas` — `schemas/*.v1.json` + validator + retry-once rule (pure functions, test-first with good/bad fixtures).
9. `schema-analysis-runs` — `analysis_run`, `output`, `checklist_item`; tick endpoint contract.
10. `markdown-export` — deterministic JSON→Markdown renderer (+ `.json` download); golden-file tests.
11. `compare-views` — `comparison*` tables and `v_run_compare` / `v_analysis_compare` views.
12. `delete-and-retention` — soft delete, purge, `delete_recording` with file removal, tests for cascade.
