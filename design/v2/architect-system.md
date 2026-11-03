# detAIls v2 — System / runtime architecture

Scope: server, job runner, memory orchestration, model registry, config, layout, tests.
Not in scope: DB schema and data contracts (see section 5 for what I need from it).

## 1. TLDR
- **Keep aiohttp.** One process, two static pages (`/record`, `/analyse`), a small JSON API, **SSE** for job progress.
- **One job runner, one GPU lease.** Jobs run one at a time. Each heavy step (STT, diarization) runs in a **child process**, so memory is freed by process exit and cancel = kill. LLM steps call LM Studio; the runner loads and unloads models through `lms`.
- **Pipeline is sequential by design:** STT → unload → diarization → unload → LLM load → outputs. Peak is the biggest single model + macOS, never the sum.
- **Crash-safe by checkpoints, not by magic:** every step writes its result to disk; STT also appends per-window results. On restart, a `running` job goes back to `queued` and skips finished steps.
- **Models are config.** `config/models.toml` lists STT engines and LLMs. A new model = a new TOML block (or a new small adapter file only for a new *engine family*).

## 2. Decisions

### D1. Web server: keep aiohttp
| Why keep | Reason |
|---|---|
| Already works for WebSocket audio (v1 `live/server.py`, 154 lines) | No rewrite risk |
| Native WebSocket + streaming responses (SSE) + static files | All we need |
| Async, single process, low overhead | Fits one user, one Mac |

Rejected: FastAPI (adds a dependency stack for features we do not use; it would give OpenAPI docs only). If the user later wants Swagger docs, this is easy to swap because handlers are thin.

### D2. Components
```
Brave ──HTTP/SSE/WS──► aiohttp app  (127.0.0.1 only)
                        ├─ api/        thin handlers (validate → call service)
                        ├─ services/   recordings, jobs, models (pure Python, testable)
                        ├─ runner/     JobRunner (asyncio task) + GpuLease
                        ├─ engines/    STTEngine adapters      ─► child process per STT job
                        ├─ llm/        LLMBackend adapters     ─► LM Studio :1234
                        └─ store/      DB or files (other architect)
```
- **Only the runner touches the GPU.** The v1 `ThreadPoolExecutor(max_workers=1)` idea becomes a proper `GpuLease` (see D4).
- Bind to `127.0.0.1` (as v1). No auth needed for local-only; stated as a risk in section 4.

### D3. Job runner (queue, resume, cancel, progress)
**Job model (what the runner needs):**
`job{id, recording_id, params{stt_id, llm_id, outputs[]}, status, step, step_progress, error, created, started, heartbeat}`.
`status`: `queued | running | cancelling | cancelled | failed | done`.
Steps are an ordered list, e.g. `[prepare, stt, diarize, llm:summary, llm:notes, llm:checklist, llm:diagram, llm:tags, assemble]`. The user's chosen outputs decide which `llm:*` steps exist.

| Concern | Decision | Reason |
|---|---|---|
| Queue | In-DB/in-file list, FIFO, **one worker task**, `max_concurrent = 1` | One GPU, 32 GB. Parallel = swap |
| Process model | STT and diarization run as **child processes** (`python -m details.worker stt <job> <engine>`). LLM steps run in the main process (HTTP to LM Studio) | MLX/ONNX memory is only reliably freed when the process ends. Kill is a hard cancel. A crash in native code does not kill the web server |
| Resume | Step result = a file + a "step done" mark, written atomically (write `.tmp`, then rename). On start: jobs `running` → `queued`; runner skips done steps | Restart safe with no extra infrastructure |
| STT resume | The worker appends one JSON line per audio window (`stt.partial.jsonl`: start, end, text). On resume it continues from the last end time | A 2.5 h file with large takes ~25 min (estimate, not measured: 9.7 s per 77 s = 0.126x real time → ~19 min). Losing it at minute 18 hurts |
| LLM resume | Each output (`summary`, `notes`…) is its own step. Map-reduce chunk results are cached per chunk | A failed output does not redo the others |
| Cancel | `POST /api/jobs/{id}/cancel` → status `cancelling` → SIGTERM child, 5 s, then SIGKILL → LM Studio request aborted by closing the HTTP connection → `cancelled`. Partial files kept (marked partial) | Predictable; user can retry |
| Retry/failure | Step failure = job `failed` with the step name and last error. `POST /api/jobs/{id}/retry` resumes from that step. No auto-retry loops | Visible, no silent GPU burn |
| Heartbeat | Worker prints a progress line every ~5 s; runner updates `heartbeat`. No heartbeat for 120 s → treat as hung, kill, mark failed | Hung Metal call is a real case on macOS (risk, not measured here) |
| Priority | Recording has priority over jobs. If a live session is open, the queue is **paused** (job stays `queued`, page shows "waiting for recording") | The live page needs the GPU for turbo. See D4 |

**Progress transport: SSE.**
| Option | Verdict |
|---|---|
| **SSE** `GET /api/jobs/{id}/events` | **Chosen.** One-way server→page. Browser `EventSource` reconnects by itself and sends `Last-Event-ID`, so a page reload or restart continues. Plain JS, 10 lines |
| WebSocket | Rejected for progress: two-way not needed; reconnect logic is manual. **Kept only** for live audio upload (v1) |
| Polling | Kept as the fallback: `GET /api/jobs/{id}` returns the same state. Needed anyway for tests and `curl` |

Event shape (small, stable): `{"seq":42,"job":"…","step":"stt","pct":37.5,"msg":"window 12/32","eta_s":610}`. Events are derived from job state, so a reconnecting client first gets the current state, then new events. No need to store the event log.

### D4. Memory orchestration (32 GB)
**Rule:** at most one heavy model resident at a time. The runner loads what the next step needs and unloads the previous one first.

Budget (all values **estimate, not measured**, unless marked "brief" = from BRIEF.md, or "v1" = measured in v1 README):

| Item | RAM | Source |
|---|---|---|
| macOS + Brave + other apps | ~8 GB | brief |
| Whisper large-v3 (MLX) | ~3.5 GB | brief (estimate) |
| Whisper turbo (MLX) | ~1.8 GB | v1 measured |
| Diarization model (pyannote-class) | ~1–2 GB | estimate, not measured |
| Parakeet-Greek 0.6B (ONNX, CPU) | ~2–3 GB | estimate, not measured |
| Gemma 4 E4B (LM Studio) | ~5–6 GB incl. KV | estimate, not measured |
| Gemma 4 26B-A4B QAT | ~14 GB weights; **21.6 GB total system in use** | brief |
| Qwen3.6-35B-A3B | does not fit without swap | brief |
| Audio buffers (2.5 h, 16 kHz mono float32) | ~0.6 GB (2.5×3600×16000×4 B) | arithmetic |
| LM Studio KV cache at 32k context | extra, grows with context | unknown, must be measured |

Peak per step (estimate, not measured):
| Step | Resident | Total with macOS |
|---|---|---|
| STT large | 3.5 + 0.6 | ~12 GB |
| Diarization | 2 + 0.6 | ~11 GB |
| LLM 26B QAT | 14 + KV | ~22 GB (brief) |
| **If everything stayed loaded** | 3.5 + 2 + 14 | ~28 GB → swap risk. This is what we avoid |

**Policy (`MemoryManager`):**
1. Before each step, ask `needs_gb` from the registry entry (config value, not hardcoded).
2. Preflight: read free + inactive memory (`vm_stat` or `psutil`) and swap used. If `needs_gb` > available, **unload everything we own** and re-check. If still short, fail with a clear message ("needs ~14 GB, 9 GB free; close Brave tabs or choose Gemma E4B").
3. STT child process: exits after the step → memory returned. No explicit unload code.
4. Before an LLM step: unload other LM Studio models, then load the wanted one with the configured context length.
5. Before the next STT step (new job): **unload LM Studio models** that we loaded (`lms unload <identifier>`). Never touch models we did not load, unless `lmstudio.exclusive = true` (default false, user's own LM Studio chats stay safe).
6. After the last LLM step: unload (default) or keep (`keep_warm = true` for repeated comparison runs on the same LLM).
7. **Swap guard:** sample swap-used before and after each step; if it grew by more than a threshold (config, default 1 GB), log a warning in the job and show it on the page. This is the measurement that makes the table above honest.
8. **Comparison mode** (same recording, N models): order jobs so that equal models are adjacent (all STT first, then all LLM per STT result). Fewer loads. Runner does this only inside one "batch" request.

**LM Studio control:**
| Action | Tool | Note |
|---|---|---|
| List what is on disk / loaded | REST `GET /api/v0/models` (has load state) or `lms ps` / `lms ls` | brief says `/api/v0` exists; verify fields in a spike |
| Load | `lms load <model> --identifier details-<id> --context-length N` | The identifier lets us find "ours" later |
| Unload | `lms unload <identifier>` / `lms unload --all` | |
| Chat | OpenAI-compatible `POST /v1/chat/completions`, `reasoning_effort:"none"` when `thinking=false` | brief |
- Docs: https://lmstudio.ai/docs/cli (lms load/unload/ps), https://lmstudio.ai/docs/app/api (REST). Exact flag names must be confirmed against the installed `lms --help` in the first slice (I did not run it).
- Wrap in `LMStudioController` with two small methods per action; a `FakeLMStudio` for tests. If `lms` is not on PATH, fall back to JIT loading by the first chat request and warn that unload is manual.

**Live page vs jobs (GPU lease):**
`GpuLease` is an asyncio lock with owners `live` and `job`. `live` wins: when `start` arrives, a running job is **not killed**, but the *next step* waits. Reason: killing a 20-minute STT because the user pressed Record is worse. The Record page shows "analysis paused until you stop". Alternative if user disagrees: pause (SIGSTOP) the child. Memory stays held, so I reject it.

### D5. Pluggable model registry
One file: `config/models.toml` (TOML: comments allowed, `tomllib` is stdlib on Python 3.12, no new dependency).

```toml
[stt.whisper-large]
engine   = "mlx_whisper"            # adapter name in engines/
label    = "Whisper large-v3"
repo     = "mlx-community/whisper-large-v3-mlx"
needs_gb = 4
langs    = ["el", "en"]
options  = { condition_on_previous_text = false }

[stt.whisper-turbo]
engine = "mlx_whisper"
repo   = "mlx-community/whisper-large-v3-turbo"
needs_gb = 2

[stt.parakeet-el]                    # future: no code if an adapter for "onnx_parakeet" exists
engine = "onnx_parakeet"
repo   = "myned-ai/parakeet-tdt-0.6b-v3-greek"
needs_gb = 3
enabled = false                      # listed in UI as "not installed"

[llm.gemma-e4b]
backend  = "lmstudio"                # OpenAI-compatible
base_url = "${LMSTUDIO_URL}"         # env expands; default from config.toml
model    = "gemma-4-e4b"             # LM Studio model id (placeholder, check `lms ls`)
context  = 16384
needs_gb = 6
role     = "cheap"

[llm.gemma-26b]
backend = "lmstudio"
model   = "gemma-4-26b-a4b-qat"      # placeholder id
context = 32768                      # brief recommends 32k
needs_gb = 22
role    = "nice"
```
- **Adding a model** = add a block. **Adding an engine family** (e.g. Canary) = one file in `engines/` that implements the interface, plus registration by name.
- `GET /api/models` returns registry entries + `installed` (checked with HF cache / `lms ls`) + `needs_gb` vs current free memory. The Analyse page builds its dropdowns from this. No model name in JS.
- The old v1 trick (scan `HF_HOME/hub` for `whisper-*`) becomes the `installed` check, not the list.

**Interfaces (sketch only):**
```python
class STTEngine(Protocol):
    id: str
    def load(self) -> None: ...
    def transcribe(self, wav: Path, *, lang: str|None, hint: str|None,
                   on_window: Callable[[Window], None]) -> Transcript: ...  # words+timestamps if engine supports
    def close(self) -> None: ...                # called before process exit
    capabilities: set[str]                      # {"word_ts", "lang_detect", "hint"}

class LLMBackend(Protocol):
    id: str
    async def ensure_loaded(self, mem: MemoryManager) -> None: ...
    async def complete(self, messages, *, max_tokens, json_schema=None,
                       thinking=False, cancel: asyncio.Event) -> Completion: ...  # text + usage + tok/s
    async def unload(self) -> None: ...
```
`Transcript` / `Window` shape comes from the data-contracts architect. I only need: segments with start/end seconds, text, optional speaker, optional word times, engine id and params.

### D6. Config: env + one file
| Layer | What |
|---|---|
| `config/config.toml` (checked in, no secrets, no paths of the user) | defaults: port, data dir *name*, LM Studio URL, thresholds, swap guard |
| `config/models.toml` | registry (D5) |
| env vars `DETAILS_*` (override file) | `DETAILS_PORT`, `DETAILS_DATA_DIR`, `DETAILS_LMSTUDIO_URL`, `DETAILS_DB_URL`, `HF_HOME` |
| `.env` (git-ignored, optional) loaded by Makefile | user-specific values |

- One `Settings` object loaded once at start; handlers get it by injection (passed to `create_app(settings)`). No `os.environ` calls elsewhere. v1's `PORT` becomes `DETAILS_PORT` (keep `PORT` as alias for one release).
- Data (recordings, transcripts, outputs) lives under `DETAILS_DATA_DIR` (default `~/details-data`, **outside the repo**, so nothing can reach git). v1 `sessions/` stays git-ignored and is imported once (slice 9).
- Startup validates config and prints the resolved port, data dir, registry count, LM Studio reachability. Fails fast on bad paths.

### D7. Folder layout (v2 on `main`)
```
details/
  Makefile  pyproject.toml  README.md
  config/            config.toml  models.toml
  src/details/
    app.py           create_app(settings)
    settings.py
    api/             recordings.py jobs.py models.py events.py (SSE)
    services/        recordings.py jobs.py pipeline.py (step list builder)
    runner/          runner.py lease.py memory.py
    engines/         base.py mlx_whisper.py  (onnx_parakeet.py later)
    llm/             base.py lmstudio.py  prompts/ (text files)
    worker/          __main__.py  (child process entry)
    store/           (data architect)
  web/
    record/          index.html record.js record.css
    analyse/         index.html analyse.js
    shared/          base.css sse.js
    live-v1/         index.html   (v1 page, unchanged)
  eval/              score.py script_meeting_el_en.md   (kept; reused)
  tests/             unit/ contract/ integration/(marked, needs GPU)
```
**v1 live page:** keep as `web/live-v1/` served at `/live` for as long as the Record page lacks live text. Its WebSocket handler moves to `api/live_ws.py` with minimal edits (use `Settings`, use the `GpuLease`). v1 is also tagged `v1.0`. Record page may later adopt live partials (out of scope here). `eval/score.py` stays; the Analyse page can reuse `score()` for the scripted takes.

### D8. Makefile
Same verbs as v1: `make setup` (venv, deps, **model downloads from registry**), `make run` (prints resolved port, per global workflow), `make test` (unit+contract, no GPU), `make test-gpu` (marked integration), `make lint`. `setup` adds a check that `lms` exists and prints the `lms ls` result.

### D9. Testing strategy (tests first)
| Layer | Needs GPU? | What is tested | Fakes |
|---|---|---|---|
| Settings/registry | no | env overrides file; bad TOML → clear error; unknown engine name | tmp files |
| Job state machine | no | queued→running→done; cancel mid-step; crash → resume skips done steps; retry from failed step; paused while live | `FakeSTTEngine` (returns canned windows, can sleep or raise), `FakeLLMBackend` |
| MemoryManager | no | unload-before-load order; preflight fails when short; swap guard warns | `FakeLMStudio` records calls; `FakeMemInfo` returns numbers |
| GpuLease | no | live blocks next step, job not killed | asyncio only |
| SSE | no | event sequence, `Last-Event-ID` replay of current state | aiohttp test client |
| Pipeline builder | no | outputs chosen → step list; chunking for long transcripts | fixtures |
| LMStudioController | no | command lines it builds; parse of `/api/v0/models` | recorded JSON sample |
| Engine adapters | **yes**, marked | 10 s clip transcribes; child process exits and RAM drops | real, `pytest -m gpu`, run by hand |
| Crash test | no | start a real child with FakeEngine, `kill -9` runner, restart, assert resume | subprocess with fake |

Rule: every runner behaviour above has a red test before code. `make test` must pass on any machine without models.

## 3. Rejected options
| Option | Why not |
|---|---|
| Celery / RQ / Redis | Extra daemon for a one-user, one-GPU app |
| Postgres `LISTEN/NOTIFY` as the queue | Fine later; for now the queue is a table/file read by one worker. Not needed for 1 worker |
| Keep models in-process and call `mx.clear_cache()` | MLX memory is not reliably returned to the OS; child process is simple and testable (risk: not measured by me) |
| Run STT and LLM at the same time "to be faster" | Peak ~28 GB, swap. LLM needs the finished transcript anyway |
| Load all 3 model types and rely on LM Studio TTL auto-evict | Eviction timing is not under our control; use as a safety net only |
| WebSocket for progress | See D3 |
| Docker/OrbStack for the app | MLX needs Metal on the host. Only Postgres stays in OrbStack |
| FastAPI / Blazor front end | See D1 and brief (plain HTML/JS) |

## 4. Risks and caveats (change decisions)
- **All memory numbers are estimates.** First slice must include a measuring script (RSS + swap per step on a real 2.5 h file). If Whisper large or the KV cache is bigger, the 26B model may force "close Brave" for the LLM step.
- **LM Studio CLI flags and REST fields are unverified by me.** Spike in slice 3 against the installed version before relying on them. The `FakeLMStudio` hides drift, so keep one real contract check in `test-gpu`.
- **2.5 h of Greek is a long LLM input.** Estimate, not measured: Greek is token-heavy, so a 2.5 h transcript is likely 50k+ tokens, above the 32k context. The pipeline needs map-reduce chunking; this is the LLM/prompt architect's job, the runner only caches per chunk.
- **Child-process design costs model load time per job** (large-v3 load a few seconds, estimate). Comparison batches of one STT model on many files pay it each time. Accept; optimise only if measured as a problem.
- **Live vs job conflict:** a user pressing Record while a long job runs waits for the next step boundary. A 19-minute STT step (estimate) is one step. Mitigation: STT is windowed, so allow "pause at next window" (cheap to add: worker checks a flag file).
- **Local-only security:** bound to 127.0.0.1, no auth. Any local process can call the API. Acceptable for one user; do not bind 0.0.0.0.
- **Brave and 16 kHz capture** is the Record architect's area; the server only needs to accept the saved file (see needs below).
- `lms unload --all` would hit models the user opened for other work; default is "only ours" by identifier.

## 5. What I need from the data/DB architect
- A **recording** record: id, file path (under `DETAILS_DATA_DIR`), duration, sample rate, format, created.
- A **job** record with the fields in D3 and a way to update `step`, `step_progress`, `heartbeat` often (every ~5 s) without heavy writes.
- **Step artifacts:** a stable per-job directory (`jobs/<id>/<step>/…`) and a "step done" mark; transcript/segment/output formats are theirs.
- A rule that **engine id + params + LLM id + prompt version** are stored with each result (needed for model comparison).
- If Postgres: a plain connection URL from `DETAILS_DB_URL`; the runner must also work with a files-only store in tests (store interface, in-memory fake).

## 6. Open questions (max 5)
1. Live priority: OK that Record **pauses the analysis queue at the next step** (job not killed)?
2. Should the app unload **only its own** LM Studio models (my default), or may it clear everything for max RAM?
3. Is it OK to keep the v1 live page at `/live` until Record has live text, or fold it now?
4. Data dir outside the repo (`~/details-data`) acceptable? (Safer for privacy; v1 `sessions/` would be imported once.)
5. For comparisons, do you want "same recording × many models" as one **batch** (runner reorders to save loads), or always single jobs?

## 7. OpenSpec change slices (system angle)
| # | Name | One line |
|---|---|---|
| 1 | `settings-and-layout` | `src/details` package, `Settings` (env + `config.toml`), Makefile verbs, `create_app`; v1 page served at `/live` unchanged |
| 2 | `model-registry` | `models.toml` loader, `GET /api/models`, `installed` check, fakes |
| 3 | `lmstudio-controller` | list/load/unload via `lms`+REST, spike against installed version, `FakeLMStudio` |
| 4 | `memory-manager` | preflight, unload-before-load policy, swap guard, measuring script (real numbers replace the estimates) |
| 5 | `job-runner-core` | job state machine, single worker, step checkpoints, resume on restart, cancel/retry, with fake engines |
| 6 | `job-progress-sse` | `/api/jobs/{id}/events` + polling endpoint, `Last-Event-ID` |
| 7 | `stt-worker-process` | child-process STT with `mlx_whisper` adapter, windowed jsonl checkpoint, heartbeat, kill-cancel |
| 8 | `gpu-lease-live` | live page + record page share the lease; queue pauses while recording |
| 9 | `llm-steps` | LLMBackend for LM Studio, one step per output, per-chunk cache (prompts from the LLM architect) |
| 10 | `diarization-step` | pluggable diarization engine in a child process, same interface family |
| 11 | `parakeet-onnx-engine` | second STT family added only via registry block + adapter, proves "config not code" |
| 12 | `v1-sessions-import` | one-time import of `sessions/*.wav` into the data dir |
