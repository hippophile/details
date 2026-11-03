# detAIls v2 — merged design

Built from 5 role docs in this folder: `designer.md`, `architect-system.md`, `architect-data.md`, `ml-engineer.md` and `audio-engineer.md`. Each has the full reasoning and sources. Estimates are labelled; only the measured numbers come from real runs.

## 1. TLDR
- **3 plain pages + 1 server:** **Record** → **Library** → **Analyse**. Shared nav, the v1 look, vanilla JS. The only library is mermaid.js, vendored locally.
- **Record:** the server writes a crash-safe WAV *while* you talk. On Stop it turns it into lossless FLAC. Test-script tabs stay.
- **Analyse = runs.** One run = speech model, then (optionally) an AI model, producing the outputs you tick: debrief, notes, checklist, tags, later speakers and diagrams. Every run is kept and labelled with model + time. Compare = tick 2 runs.
- **One job at a time on the GPU, steps in sequence:** speech, unload, speakers, unload, AI. This never swaps on 32 GB. Jobs survive closing the tab and restarting the server.
- **Storage = hybrid:** audio files on disk, everything else in **PostgreSQL**. Runs are append-only, so nothing is overwritten.

## 2. Architecture
```
Brave ── HTTP / SSE / WS ──► aiohttp (127.0.0.1)
  record.html  library.html  analyse.html     ├─ api/        thin handlers
                                              ├─ services/   recordings, runs, models
                                              ├─ runner/     1 worker + GpuLease + MemoryManager
                                              ├─ engines/    STT adapters → child process per job
                                              ├─ llm/        LM Studio client (json_schema) ─► :1234
                                              └─ store/      Postgres (psycopg 3, plain .sql migrations)
config.toml + models.toml + DETAILS_* env      ~/details-data/recordings/<uuid>/{master.flac, audio.16k.wav}
```
- **Child process per STT/diarization job:** memory is truly freed when the process exits, and cancel = kill.
- **LM Studio models** are loaded and unloaded via `lms`. The app only unloads the models it loaded itself.
- **Progress:** SSE, which reconnects by itself, with polling as a fallback. The WebSocket is used only for live audio.
- **Checkpoints:** each step writes its result atomically, and STT also appends per 30 s window. After a crash, the runner resumes at the last finished step or window.

## 3. Conflicts between the roles, and how I resolved them
| Topic | Option A | Option B | **Chosen** | Why |
|---|---|---|---|---|
| Data folder | `~/Library/Application Support/detAIls` (data) | `~/details-data` (system) | **`~/details-data`**, set via `DETAILS_DATA_DIR` | Visible in Finder, easy for someone new to Mac. Outside the repo, so never in git |
| Model list format | `models.json` (ML) | `models.toml` (system) | **`models.toml`** | One config format with `config.toml`. Allows comments |
| Pages | 2 pages (system) | 3 pages (designer) | **3: Record, Library, Analyse** | Picking a recording gets its own clear place |
| Checklist ticks | projection table | patch the jsonb | **patch jsonb + a simple index** | Fewer tables. Slice 6 can still change it |
| v1 live page | fold in now | keep at `/live` | **keep `/live` until Record has live text** | Nothing you're happy with breaks |

## 4. Recording formats: your question
| Format | Per hour | Quality for speech-to-text | Plays in QuickTime | Use |
|---|---|---|---|---|
| **WAV 16 kHz 16-bit** | 115 MB | perfect (reference) | yes | ✓ written *while* recording (crash-safe) |
| **FLAC 16 kHz** | ~69 MB (60% of WAV, measured on one clip) | identical (lossless) | probably (macOS 11+, to test) | ✓ **master after Stop** |
| M4A / AAC 64k | 29 MB | small loss (no Whisper test found) | yes, best | export to share or play |
| Opus 32k (WebM) | 14 MB | about +2% relative errors (non-Whisper test) | no | smallest, rarely needed |
| MP3 64k | 29 MB | worst lossy, about +10% relative errors (IBM test, not Whisper) | yes | only if a tool demands it |
| WAV 48 kHz | 346 MB | same as 16 kHz (Whisper resamples to 16k internally) | yes | ✕ 3× the disk for nothing |

A 2.5 h meeting is 288 MB while recording and about 170 MB as FLAC (estimate). A `codec-wer-experiment` slice will measure whether lossy formats actually hurt Whisper on your voice.
**v1 bug found:** the v1 recorder downsamples 48k to 16k without a filter, which causes aliasing. v2 uses `AudioContext({sampleRate:16000})` instead.

## 5. Models
**Speech to text:** a registry in `models.toml`; adding a model = one config block.
| Order | Model | How it runs | Greek (source) |
|---|---|---|---|
| now | Whisper turbo / large-v3 | mlx-whisper (works) | **measured**: 26.7% / 18.8% wrong on your take |
| v2.1 #1 | Parakeet-Greek fp32 (myned-ai) | onnx-asr, CPU | self-reported 8.9% vs large 16.9% on Common Voice. Never int8 |
| v2.1 #2 | OpenCouncil LoRA → merged into large-v3 → MLX | one-time convert script (unverified) | 17.95 vs 19.88 on council meetings |
| v2.1 #3 | sam8000 turbo-Greek | convert to MLX | 11.06 FLEURS, but trained on FLEURS |
| ✕ | Canary-1B-v2 | NVIDIA stack, not realistic on Mac | — |

**Who talked:** pyannote **community-1**, run offline after STT and aligned by time overlap. It needs a **free Hugging Face token once** (gated model). Expect about 17–20% speaker errors on meetings (cited), so speakers are marked "auto" and can be renamed or merged.

**AI write-up:** LM Studio `/v1/chat/completions` with `response_format: json_schema`. **Verified live** on Gemma E4B: valid Greek JSON.
- **One call per output kind**, each with its own JSON schema. Code renders the Markdown and Mermaid, never the LLM directly.
- **Every item quotes the transcript**, and code checks the quote exists. This is the hallucination guard.
- **Clean-up returns a list of edits only**, applied by code and shown as a diff. No silent rewriting.
- **Long meetings:** map-reduce over chunks of about 8k tokens, cached per chunk. Thinking is off by default.
- **"Cheap" (E4B) vs "nice" (26B QAT)** is measured with a small hand-made key: action-item recall/precision, quote validity, schema validity and time.

## 6. Data (Postgres, dedicated DB `details`)
`recording → stt_run → (diarization_run) → analysis_run → output`, plus `prompt_template`, `comparison` and `eval_result`.
- **Append-only:** a re-run makes a new row. Mutable: checklist done-state, title, tags, speaker names.
- **Prompts:** git files `prompts/<kind>/vN.md`. Each run stores the full rendered prompt + model + params, so it is reproducible.
- **Migrations:** numbered `.sql` files + a ~30-line runner. No ORM.
- **Import:** an importer brings in v1 `sessions/` (idempotent, dry-run first, copies rather than moves).
- **Deleting:** a 30-day trash, then purge. `make backup` = `pg_dump`, because recordings are never in git.

## 7. Plan: OpenSpec changes (tests first, one at a time)
**v2.0, the core you asked for**
| # | Change | Delivers |
|---|---|---|
| 1 | `v2-foundation` | `src/details` package, `config.toml` + env, Makefile, Postgres DB + migration runner, data dir, v1 kept at `/live` |
| 2 | `recording-capture` | Record page: title, timer, crash-safe WAV → FLAC, recovery after a crash, 16 kHz capture fix, script tabs, "Saved" card, v1 import |
| 3 | `library-page` | List / search / play / rename / delete (trash) recordings, import an audio file (ffmpeg) |
| 4 | `jobs-and-models` | `models.toml` registry, job runner (1 worker, checkpoints, cancel/retry), SSE progress, LM Studio load/unload, memory guard |
| 5 | `stt-runs` | Analyse page: pick a speech model → run → transcript view, runs list labelled with model + time |
| 6 | `llm-outputs` | Pick an AI model + outputs: debrief, notes, tickable checklist, tags. JSON schemas, quote check, chunking, Markdown export |
| 7 | `compare-runs` | Tick 2 runs: transcript word diff + side-by-side outputs + timings (+ WER if a script was used) |

**v2.1:** one-click speech + AI in a single run · `speakers-diarization` (pyannote) · `diagrams-mermaid` · `more-stt-models` (Parakeet-Greek, OpenCouncil, sam8000).
**v2.2:** `meeting-audio-capture` (tab audio, Swift Core Audio tap helper, stereo with L = me and R = them) · `audio-experiments` (codec test, VAD pre-filter, M4A/MP3 export).

## 8. Risks that change decisions
- **Unmeasured new models:** all Greek numbers for new models are self-reported. The first thing v2.1 does is score them on your recordings.
- **LM Studio CLI unchecked:** `lms` flags and REST fields weren't run by the system architect, so change 4 starts with a short test against your installed version.
- **Speaker tests need a second person:** you reading 3 voices is one voice to a speaker model.
- **Desktop Teams/Zoom audio needs a small Swift helper.** Whether it gets the macOS audio permission when run from the Terminal is untested.
- **2.5 h analysis on 26B QAT:** 10–30 min (estimate). E4B is faster, at lower quality, which is the point of comparing them.

## 9. Questions for you (decisions only you can make)
1. **Hugging Face:** OK to make a free account and token once, for speaker detection? (Don't screenshot the token.)
2. **Meetings:** are they in **desktop apps** (Teams/Zoom → Swift helper) or **in the browser** (Meet/Teams web → no native code)?
3. **Data folder:** is `~/details-data` OK?
4. **Defaults:** for long meetings, default to **nice** (26B QAT, slower) or **cheap** (E4B), with the other one a click away?
5. **Speaker test:** can you record 5 min with a second real person?
