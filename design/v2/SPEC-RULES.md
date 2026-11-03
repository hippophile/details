# Rules for writing the v2.0 OpenSpec changes (shared by all spec writers)

Read first: `design/v2/DESIGN.md` (the merged design, which **wins** over the role docs), then the role docs it cites for your area, then the v1 code (`live/server.py`, `live/index.html`, `eval/score.py`, `Makefile`, `tests/`).

## User decisions (final)
- **Data folder:** `DETAILS_DATA_DIR`, default `~/details-data`. Models go in `~/details-data/models/`; Parakeet-Greek fp32 and the OpenCouncil LoRA are already downloaded there.
- **Database:** PostgreSQL, from `DATABASE_URL` / `DATABASE_URL_TEST` in a git-ignored `.env`. The DBs `details` and `details_test` already exist. psycopg 3, plain `.sql` migrations, no ORM.
- **Output language** follows the transcript language by default, with an English switch per run.
- **Presets:** both "cheap" (Gemma E4B) and "nice" (Gemma 26B QAT) are offered; neither is a forced default.
- **Meetings for v2.0:** in-room only, mic-only capture. Tab audio and the Swift helper are v2.2 and **out of scope**.
- **v1 stays working at `/live`.** v1 is archived as tag `v1.0`.

## Shared contracts (every change must respect these, so they fit together)
- **Package layout:** `src/details/{settings.py, app.py, db.py, api/, services/, runner/, engines/, llm/, store/}`, with web pages in `web/{record,library,analyse,shared}/`. Tests go in `tests/` (pytest). GPU tests are marked `@pytest.mark.gpu` and are excluded by default.
- **Config:** `config.toml` (repo) + `models.toml` (repo), with `DETAILS_*` env vars overriding. Nothing hardcoded: ports, paths, model ids and the LM Studio URL all come from config.
- **Migrations:** `db/migrations/NNNN_name.sql`. Fixed numbers per change so nothing collides:

  | Change | Migration number |
  |---|---|
  | v2-foundation | 0001 (`schema_migration` only) |
  | recording-capture | 0002 (`script`, `recording`) |
  | jobs-and-models | 0003 (`job`) |
  | stt-runs | 0004 (`stt_run`, `transcript_segment`) |
  | llm-outputs | 0005 (`prompt_template`, `analysis_run`, `output`, `tag`) |
  | compare-runs | 0006 (`comparison`, `comparison_item`, `eval_result`) |
  | library-page | **no new tables** (it only uses `recording.deleted_at` from 0002) |

- **API prefix:** `/api/...` with JSON responses. Pages are static files served by aiohttp, bound to 127.0.0.1.
- **Capabilities** (one spec folder each):

  | Change | Capability folder(s) |
  |---|---|
  | v2-foundation | `app-foundation` |
  | recording-capture | `recording` |
  | library-page | `library` |
  | jobs-and-models | `job-runner` and `model-registry` |
  | stt-runs | `stt-runs` |
  | llm-outputs | `analysis-outputs` |
  | compare-runs | `run-comparison` |

- **Dependency order:** foundation → recording-capture → library-page → jobs-and-models → stt-runs → llm-outputs → compare-runs. Each proposal states which of these it depends on.

## OpenSpec format (`openspec validate --strict` must pass)
- Files: `openspec/changes/<name>/{proposal.md, tasks.md, specs/<capability>/spec.md}`, plus `design.md` only if the approach needs arguing.
- Spec deltas use `## ADDED Requirements`, then `### Requirement: <title>`. **The first body line must contain SHALL or MUST.** Every requirement needs at least one `#### Scenario:` written as `- **GIVEN** / **WHEN** / **THEN** / **AND**` bullets.
- **proposal.md** sections:
  1. **Driver** (the user's own words, from DESIGN.md / BRIEF.md)
  2. **Depends on**
  3. **Measured blast radius** (real counts from the repo, dated 2026-11-03: which files are touched or created)
  4. **Decision** (numbered, file-level)
  5. **Verification** (how it is proven, including a negative case)
  6. **Caveats**
- **tasks.md:** numbered `- [ ]` checkboxes, **test-first**. Task 1 writes the failing tests, and a step says "run, observe failure". Then implement. Then full suite green. Then a manual check (e.g. a screenshot of the page or a curl). Keep each change small enough for one implementer in one session.
- **Scope:** keep it tight. When in doubt, move it to v2.1. No GPU-heavy tests in the default suite; use fakes.
- Run `openspec validate <name> --strict` yourself, and fix the change until it passes.
- **Do not write application code. Do not commit.** Write only inside `openspec/changes/<your changes>/`.
- When you're done, return 10 lines or fewer: the change names, their validate result, and any open conflicts with other changes.

## Integration contract (added after review; this wins over any proposal text)
**Steps and jobs** (owned by jobs-and-models; stt-runs and llm-outputs use these exact names)
- `steps = StepRegistry()`, registered with the decorator `@steps.register("stt_transcribe")`. Step modules are `src/details/runner/steps_stt.py` and `runner/steps_analyse.py`, imported in `create_app`.
- `JobService.enqueue(kind: str, recording_id: UUID|None, steps: list[str | {"name","params"}], params: dict = {}) -> job_id`. HTTP: `POST /api/jobs {kind, recording_id, steps, params}`.
- `StepContext` fields: `job_id`, `step_name`, `params` (step params merged over job params), `cancelled` (asyncio.Event), `step_dir` (the ONLY checkpoint dir, `<data_dir>/jobs/<job_id>/<step>/`), `data_dir`, `settings`, `memory` (MemoryManager), `progress(pct, msg)`, `spawn_child(argv)`.
  - Child processes print one stdout line at model load and at least one per window; that is the heartbeat.
- **Built-in steps** in jobs-and-models: `lm_load` (params `model_id`) and `lm_unload`, as thin wrappers over MemoryManager. STT steps call `await ctx.memory.release_for_stt()` before spawning.
- **Terminal hook:** `@steps.on_terminal("stt_transcribe")` finalizers run when a job ends cancelled or failed, including when the job is still queued. stt and llm use them to set their run rows to cancelled or failed.

**Models**
- `models.toml`: `[stt.<id>]` and `[llm.<id>]` blocks. `engine` must be one of `mlx_whisper` (STT) or `lmstudio` (LLM); an unknown engine is a load-time error with a clear message. jobs-and-models ships the 4 entries `whisper-turbo`, `whisper-large`, `gemma-cheap` and `gemma-nice`. Later changes add NO duplicate entries.
- `GET /api/models` → `{stt:[...], llm:[...], lmstudio:{reachable}}`. Each entry: `{id, kind, label, engine, role, needs_gb, installed, unavailable_reason?, hint?}`. `role` is one of `cheap|nice|fast|accurate`. `?kind=stt|llm` returns a flat list. A POST with an unknown model id returns 422.

**Pages and URLs** (owned by v2-foundation)
- `/record`, `/library` and `/analyse` serve `web/<x>/index.html`, with assets under `/<x>/`. Shared files are under `/shared/`.
- Links always use `/analyse?rec=<id>` and `/library`. There are no `*.html` URLs.

**Settings** (owned by v2-foundation)
- Nested sections `settings.<section>.<key>`, from `config.toml` `[section]`. The env override is `DETAILS_<SECTION>_<KEY>`, with a per-section validation hook.
- The LM Studio URL is `settings.lmstudio.url` (`DETAILS_LMSTUDIO_URL`). The `lms` binary path is `settings.lmstudio.cli`. There is no `[llm] url`.
- A change that adds a section lists `settings.py` as modified.
- DSNs and `.env` values are never logged. Add a negative test: a bad `DATABASE_URL` error must not contain the password.

**Database** (owned by v2-foundation)
- `psycopg_pool.AsyncConnectionPool` everywhere in the server. No sync DB calls on the event loop.
- Test fixture `clean_db`: `TRUNCATE` all tables except `schema_migration`, with `RESTART IDENTITY CASCADE`, before each test.
- Startup hooks (recovery, seed sync, purge, prompt sync, runner start) run in a fixed order after a migration check. If migrations are pending, the app gives a clear "run `make migrate`" error.
- Every FK to `recording` is `ON DELETE CASCADE`, including `job`. Purge skips recordings with a queued or running job, and removes `<data_dir>/recordings/<id>` and `<data_dir>/jobs/<job_id>` for the recording's jobs.

**Security** (owned by v2-foundation): middleware rejects a non-localhost `Host`, and rejects a mismatched `Origin` on POST/PATCH/DELETE and WebSocket upgrades. Add negative tests.

**Test helpers:** the package `tests/fakes/{__init__,engines,lmstudio}.py` is created by jobs-and-models. llm-outputs extends `FakeLMStudio` with an HTTP mode and creates no new fake module.

**Decisions on open items**
- **stt-runs:** adopts the importer's `v1-transcript.txt` as an `stt_run` (`engine='mlx_whisper'`, `model_id='unknown (v1 import)'`, `status=done`, no segments). It also wires `runs_count` (COUNT of stt_run) into the library list.
- **`tag` table:** dropped from 0005. Tags live in `output.content`; a tag table is v2.1.
- **compare-runs:** adds `GET /api/comparisons?recording_id=`. When a run is deleted, comparisons left with fewer than 2 items are deleted by the service, with a test.
- **One-click STT + AI in a single run:** deferred to v2.1, and noted in DESIGN.
- **Skipped tests:** JS tests skipped for lack of node print a visible SKIP reason in `make test`.
