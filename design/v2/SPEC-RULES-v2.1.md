# Rules for the v2.1 "insights + live" OpenSpec changes

Everything in `design/v2/SPEC-RULES.md` still applies: format, test-first, proposal sections, the Integration contract, security, and no secrets in logs. v2.0 is **implemented and archived**, so read the real code in `src/details/`, `web/`, `tests/` and the current specs in `openspec/specs/*`. Design against what exists, not against the design docs.

## What the user asked for (2026-11-04): "do them all", then "propose review apply"
| # | Feature | User-visible result |
|---|---|---|
| A | Live pipeline view | Each step of a job is shown live: name, status, duration, model loaded, tokens in/out, tok/s, RAM |
| B | Logs panel | Per job: worker lines, model load/unload, LM Studio calls (model, tokens, time, status), errors. Live tail plus download |
| C | Prompt inspector | For every analysis output: "View prompt" (the exact rendered prompt), "View raw reply", tokens, params |
| D | AI writes live | Analysis output text appears token by token while Gemma writes (LM Studio streaming). Validation still happens at the end |
| E | Live meeting assistant | During recording on `/record`: live transcript (Whisper turbo, like the v1 `/live` page, but inside the Record page and never endangering the crash-safe recording). Every ~2 min (configurable), **Gemma E4B (cheap) only** updates rolling notes, action items and tags on screen. After Stop, the normal Analyse flow still exists |
| F | Prompt A/B | Edit a prompt in the UI, save it as a new version (`prompts/<kind>/vN.md` + a `prompt_template` row; never edit an old version), run v1 vs v2 on the same transcript, compare in the existing compare view |

## Changes, order, migration numbers, capabilities
| Order | Change | Features | Migration | Capability |
|---|---|---|---|---|
| 1 | `run-insights` | A + B + C | 0007 if needed (prefer files in `step_dir` for logs; say why) | `run-insights` |
| 2 | `llm-streaming` | D | none | `analysis-streaming` |
| 3 | `prompt-ab` | F | 0008 if needed | `prompt-versions` |
| 4 | `live-assistant` | E | 0009 if needed | `live-assistant` |

`live-assistant` depends on `llm-streaming` only if it chooses to stream. Say so.

## Constraints that matter here
- **Memory on 32 GB:**
  - Live mode = Whisper turbo (~1.8 GB measured) + Gemma E4B (~7 GB) + the recorder.
  - **Never** the 26B in live mode.
  - While live mode is on, the job queue stays paused (GpuLease priority, which already exists).
  - Show a clear message if LM Studio is not reachable. Recording must still work.
- **Recording safety is sacred:** the live features must never block, slow or break the WAV/FLAC writer. If live STT or the LLM fails, the recording continues and the page says so.
- **Quote rule:** keep the rule that every AI item carries a verbatim transcript quote checked by code, also for the live rolling notes (checked against the live transcript so far).
- **Prompts:** edits never overwrite an existing version. A run always records the exact template version and the rendered prompt (already true).
- **Brave is the test browser** (`tests/brave-playwright.json`). Headless mic = override `getUserMedia` to play a wav (see `design/v2/SPEC-RULES.md` notes and the earlier e2e approach). The default suite uses fakes; real GPU or LM Studio tests are marked `@pytest.mark.gpu`.
- **Known v2.0 issues you may fold in when they are inside your change's area** (say so in the proposal):
  - Gemma E4B invented a year in a due date (prompt v2 / year constraint) → `prompt-ab` can ship `checklist` v2 as its first real A/B.
  - On a failed or cancelled analysis, the app-loaded LM Studio model stays loaded → `run-insights` may add an `on_terminal` unload.
- Write only inside `openspec/changes/<your changes>/`. No application code. No commit. Run `openspec validate <name> --strict` until it passes.
- Return 10 lines or fewer.
