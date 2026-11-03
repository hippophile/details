# detAIls v2 — design brief (shared by all 5 roles)

## What the user wants (their words, cleaned up)
1. **Record page (HTML, simple, nothing fancy):** record a meeting, switch between test-script tabs while recording, and **save the recording** (to be decided: which formats are available, with pros and cons).
2. **Analyse page (a second HTML page):** for now the user **picks a saved recording by hand**, then chooses:
   - a **speech-to-text model**: Whisper turbo / Whisper large / other "whispers" such as Parakeet-Greek, Canary, the OpenCouncil LoRA…
   - an **AI model** for the write-up: a "cheap" one (Gemma 4 E4B), a "nice" one (Gemma 4 26B-A4B QAT) or others (Qwen3.6-35B-A3B…).
   …and turns it into: **debrief, notes, lists, real notes with checklists, diagrams, who talked (speakers), tags**, etc.
3. The point is to **compare models** (STT and LLM) on the same recording, because different tools suit different jobs.
4. v1 stays archived (git tag `v1.0`, branch `v1`). v2 is built on `main`.

## Hard constraints
- **Hardware:** MacBook Pro M1 Pro, 32 GB unified memory, macOS **14.6** (so Core Audio taps are available, from 14.4+). The browser is **Brave** (Chromium).
- **100% local and private.** No cloud APIs. Recordings never go into git.
- **Database:** if one is needed, **PostgreSQL only** (Postgres 18 already runs in an OrbStack Docker container called `postgres`). No SQLite. A plain-files option is fine if you argue for it.
- **No hardcoded config.** Ports, paths and model lists come from env vars or a config file.
- **Simple HTML/CSS/JS** pages, not a SPA framework. The user is a .NET/Blazor dev who works alone. The current stack is Python (aiohttp + mlx-whisper).
- **Process:** this goes to OpenSpec changes later (`openspec/changes/<name>/`), and tests come first. So split your proposals into **small, independently shippable change slices**.
- The user is a non-native English speaker. Use short sentences, tables over prose, and put the recommendation first.

## What exists today (read before designing)
- Repo: `/Users/golem/code/play/details`. Read `README.md`, `live/server.py`, `live/index.html`, `eval/score.py`.
  - v1 live page: the browser captures 16 kHz PCM → WebSocket → mlx-whisper. Live partials, commit on pause, a full pass on Stop, saves a WAV to `sessions/`.
- Research on Greek STT: `research/greek-stt-candidates.md` and `research/notes/*.md`, including `greek_specific_deeper.md` (Parakeet-Greek, the OpenCouncil LoRA, a turbo Greek fine-tune).
- **LM Studio** runs an OpenAI-compatible server on `http://localhost:1234` (`/v1/chat/completions`; `/api/v0/...` returns tok/s stats). `"reasoning_effort":"none"` turns thinking off. On disk:
  - Gemma 4 E4B: 32 tok/s, light.
  - Gemma 4 26B-A4B QAT 4-bit: 41 tok/s; about 21.6 GB of total system RAM in use when loaded; a 32k context is recommended.
  - Qwen3.6-35B-A3B: 49 tok/s, but uses swap.
  - Gemma 4 12B.
- **Measured STT on one real take (1.5 min, 3 voices by one person):**

  | model | words wrong | tech words right | speed |
  |---|---|---|---|
  | Whisper large-v3 | 18.8% | 81% | 9.7 s per 77 s of audio |
  | Whisper turbo | 26.7% | 62% | 4.2 s, 1.8 GB RAM |

  Typical misses are English product names (Gemma, n8n, triage, AI Act).
- **Memory budget:** Whisper large uses about 3.5 GB (estimate), Gemma QAT about 14 GB, macOS + apps about 8 GB. The steps may need to run **one after another**, not all at once.

## Your output
- **Where:** write **one design doc** at `/Users/golem/code/play/details/design/v2/<your-role>.md`.
- **Structure:**
  1. TLDR (3–5 lines, recommendation first)
  2. Decisions, each with its reason
  3. Options you rejected and why (briefly)
  4. Risks and caveats that change a decision
  5. Open questions for the user (maximum 5)
  6. Proposed OpenSpec change slices from your angle: name + 1 line each
- **Length:** at most ~250 lines. Cite sources (URLs) for any external facts. Label every estimate "estimate, not measured".
- **Do not write application code.** Small snippets are allowed only to illustrate an interface. Do not edit any file outside `design/v2/`. Do not commit.
- When you're done, return a summary of 10 lines or fewer.
