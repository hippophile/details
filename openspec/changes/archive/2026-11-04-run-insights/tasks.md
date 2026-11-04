## 1. Tests first (red)
- [x] 1.1 `tests/test_run_insights_log.py`: line format and ordering across `_job` and step dirs, `after=`, `n` continues after restart, cap + truncation line, all `src` values, no prompt/reply text in LM call lines, child stderr tail.
- [x] 1.2 `tests/test_run_insights_log.py`: **negative secrets test** (exception with `postgresql://u:SeCr3tPw@db/x`, fake LM Studio 500 body with `password=SeCr3tPw`, bad `DATABASE_URL` value): the secret is absent from `log.jsonl`, `GET /log`, download, `step.json`, job `error`.
- [x] 1.3 `tests/test_run_insights_steps.py`: `step.json` lifecycle, `ctx.metrics`, `pending` / `interrupted`, resumed job keeps old records, SSE `steps` field present and old fields unchanged.
- [x] 1.4 `tests/test_run_insights_api.py`: `/steps`, `/log` (limit, after, done, 404), `?download=1` headers.
- [x] 1.5 `tests/test_run_insights_inspect.py`: retry chain, map-reduce labels, failed-call null prompt, 404.
- [x] 1.6 `tests/test_run_insights_terminal.py`: failed unloads only `details-*`; queued cancel during a running job does not unload; unload error keeps `failed`; retry reloads (FakeLMStudio `calls`); `unload_on_terminal=false` keeps the model.
- [x] 1.7 `tests/js/insights.test.mjs` and `tests/test_insights_page.py`: row formatting (`-` for missing values, tok/s, duration), ids, `textContent`-only, no hardcoded model ids.
- [x] 1.7b Update existing `tests/test_job_api.py` (~line 143, SSE key set gains `steps`); add a test that `create_app` passes one app-stored `GpuLease` to `JobRunner`; add `bind(pool)` still works; test queued-cancel with lease owner `live` does not unload; `GET /api/analyses/{gid}` `.error` in the secrets test; wait-for-lease status. For the child stderr-tail test use an inline `python -c` child (no new mode in `tests/fakes/engines.py`).
- [x] 1.8 Run `pytest tests/test_run_insights_*.py tests/test_insights_page.py` and `node --test tests/js/insights.test.mjs`; **observe and keep the failing output** (expected: import errors / 404).

## 2. Implement
- [x] 2.0 `app.py`: one `GpuLease` on the app passed to `JobRunner(lease=...)`; shared `JobLog` registry; `bind(pool, *, memory=None, settings=None, hub=None)`.
- [x] 2.1 `settings.py` + `config.toml`: `[runner] log_max_lines = 5000`, `[lmstudio] unload_on_terminal = true` (env `DETAILS_RUNNER_LOG_MAX_LINES`, `DETAILS_LMSTUDIO_UNLOAD_ON_TERMINAL`), validation (> 0, bool).
- [x] 2.2 `runner/joblog.py`: `redact()`, `JobLog(data_dir, job_id, max_lines)` append/read/iter, `n` recovery, cap; reject step names starting with `_` at registration.
- [x] 2.3 `runner/stepinfo.py` + `runner/runner.py`: write/merge `step.json` (atomic), RAM sampling in a thread, job-level log lines, redact `error`.
- [x] 2.4 `runner/steps.py`: `ctx.log(level, msg, **data)`, `ctx.metrics(**kv)`; log lines in `lm_load`/`lm_unload`; `spawn_child` logs worker lines and the stderr tail on failure. `services/jobs.py`: optional `data_dir`, log the cancel of a queued job.
- [x] 2.5 `steps_analyse.py`: wrap `chat` to log one `src=llm` line per call (also on `LLMError`) and accumulate `ctx.metrics`; `ensure_llm` before the first call; `bind(pool, memory=None, settings=None)`; terminal unload with the guards. `app.py`: pass memory/settings to `bind`, `data_dir` to `JobService`, register routes.
- [x] 2.6 `api/insights.py` (`/steps`, `/log`, download, `/inspect`); `api/jobs.py` SSE state gets `steps` (mtime cache).
- [x] 2.7 `web/analyse/insights.{js,css}` + `index.html` / `analyse.js` / `outputs.js` hooks (step table, log panel, Inspect dialog). Do not touch other UI.
- [x] 2.8 Re-run the task 1 tests: all green.

## 3. Gate
- [x] 3.1 `make test` (full suite once, GPU tests excluded); paste the summary line. JS tests without node must print a visible SKIP.
- [x] 3.2 `openspec validate run-insights --strict`.

## 4. Manual proof (screenshots read by the implementer)
- [x] 4.1 Server on a fake-backed data dir: run a 3-step job, open `/analyse?rec=<id>`, screenshot the Running card with the step table and the Log panel (Brave via `tests/brave-playwright.json`); `curl` `/api/jobs/<id>/log?after=0` and `?download=1`.
- [x] 4.2 Screenshot the Inspect dialog (Prompt tab) of a finished analysis.
- [x] 4.3 (NOT RUN as a pytest; the same check was done by hand in Brave: lms ps showed only the user model after success and forced failure) Optional `@pytest.mark.gpu`: real analyse with the loaded E4B, cancel it, check with `lms ps` that only `details-*` is gone (user's models untouched). Do not unload the user's own model.
