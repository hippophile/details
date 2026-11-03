# detAIls v2 — Product/UX design

Scope: page flow, wireframes, output views, model comparison, waiting UX.
Backend/storage/model internals are NOT decided here. Assumptions are marked **[A#]**.

## 1. TLDR

- **Three plain HTML pages, one shared top nav:** `record.html` · `library.html` · `analyse.html?rec=<id>`.
- **Analyse = "Runs".** One recording has many *runs* (STT model, or STT + LLM). Every run is kept, labelled with model + time taken. Compare = tick 2 runs, press Compare.
- **Pick outputs with chips** (Debrief, Notes, Checklist, Diagram, Speakers, Tags). Defaults: all on except Diagram. Two presets: "Cheap & fast", "Best quality".
- **Long jobs run on the server**, page is only a viewer. Close the tab = job continues; reopen = see progress. Cancel always visible.
- Only one extra lib: **mermaid.js** (diagrams). Everything else vanilla. Reason in D9.

## 2. Decisions (with reasons)

### D1. Flow

```
 ┌──────────┐  Stop+Save   ┌───────────┐  "Analyse"   ┌────────────┐
 │  Record  │ ───────────▶ │  Library  │ ───────────▶ │  Analyse   │
 └──────────┘              └───────────┘              │ (runs list)│
      ▲   "New recording"        ▲  back              └────────────┘
      └──────────────────────────┴──────────────────────────┘
```

- After Stop, Record shows a "Saved" card with two buttons: **Analyse now** and **Open library**. Reason: the most common next step is one click.
- Library is the only place to pick a recording. Analyse never shows a file picker, it gets `?rec=id`. Reason: one way to do it, URL is bookmarkable and survives reload.
- Nav bar (same on all 3 pages): `Record | Library | Analyse`. "Analyse" with no `rec` shows "Pick a recording in Library".
- **[A1]** Server gives: list of recordings, list of runs per recording, models available (from config), job status. Page does no business logic.

### D2. Record page (small change from v1)

Keep v1 layout: model card, level meter, Live, test-script tabs. Changes:
- **Remove** "Model" dropdown from the main row? No. Keep it (user is happy with v1). Add: **Save format** select (formats decided by the audio role) and **Title** input.
- Add a **timer** (mm:ss) next to the mic button, and a **Save** area after stop.
- Script tabs keep working during recording (v1 behaviour, unchanged).
- Live text is a *preview*, not the final result. Label it: "Live preview (rough). Final text comes from Analyse."

```
┌ detAIls ─────────────────────────────  Record │ Library │ Analyse ┐
│ ┌ Setup ────────────────────────────────────────────────────────┐ │
│ │ Title [ Standup 3 Nov        ]  Format [ WAV ▾ ] Live [turbo▾]│ │
│ │                                          [ 🎙 Mic on  00:00 ]  │ │
│ └───────────────────────────────────────────────────────────────┘ │
│ ┌ Level ────────────────────────────────────────────────────────┐ │
│ │ ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░  -32 dB  good                           │ │
│ └───────────────────────────────────────────────────────────────┘ │
│ ┌ Live preview (rough) ─────────────────────────────────────────┐ │
│ │ Καλημέρα. Λοιπόν γρήγορο standup…                              │ │
│ └───────────────────────────────────────────────────────────────┘ │
│ ┌ Test scripts ─────────────────────────────────────────────────┐ │
│ │ (Standup) (BPMN) (AI plan)   ← tabs work while recording      │ │
│ └───────────────────────────────────────────────────────────────┘ │
│ After Stop:                                                       │
│ ┌ ✔ Saved · Standup 3 Nov · 01:32 · 2.9 MB ─────────────────────┐ │
│ │ [ Analyse now ]  [ Open library ]  [ New recording ]          │ │
│ └───────────────────────────────────────────────────────────────┘ │
```

Phone width: cards stack, mic button full width, tabs wrap (v1 already does this).

### D3. Library page

Table on desktop, cards on phone. Columns: title, date, length, size, **runs count**, last run badge. Actions: Analyse, play, rename, delete (with "Proceed?"-style confirm dialog, no undo).

```
┌ Library ───────────────────────────────  [ Search…        ] ┐
│ Title               Date        Length  Runs  Actions        │
│ ──────────────────────────────────────────────────────────── │
│ Standup 3 Nov       03 Nov 10:02  01:32   3   ▶ [Analyse] ⋯  │
│ BPMN review         02 Nov 15:40  12:10   1   ▶ [Analyse] ⋯  │
│ AI plan (mgmt)      01 Nov 09:00  58:44   0   ▶ [Analyse] ⋯  │
│ ⚠ one job running on "AI plan (mgmt)" → [View]               │
└──────────────────────────────────────────────────────────────┘
Phone: each row = card: title (bold) / date · length · runs / buttons
```

- Audio player is native `<audio controls>` (keyboard works free).
- A banner shows running jobs on any recording. Reason: user may close the tab and come back.
- **[A2]** Recording metadata (title, length, runs count) comes from the API, not from the filenames.

### D4. Analyse page — layout

Top: recording header + player. Then **New run** card. Then **Runs** list. Then the opened run's result.

```
┌ Analyse · Standup 3 Nov · 01:32 ───────────────────────────────┐
│ ▶ ──────●────────────── 00:32 / 01:32                          │
│ ┌ New run ────────────────────────────────────────────────────┐ │
│ │ Preset (Cheap & fast) (Best quality) (Custom)               │ │
│ │ Speech-to-text  [ Whisper large-v3        ▾ ]  ~10 s·est.   │ │
│ │ Language        [ Ελληνικά ▾ ]  ☑ tech vocab hint           │ │
│ │ Write-up model  [ Gemma 4 26B-A4B QAT     ▾ ]  ~14 GB RAM   │ │
│ │ Outputs  [✔Debrief] [✔Notes] [✔Checklist] [ Diagram]       │ │
│ │          [✔Speakers] [✔Tags]  [ + Custom prompt ]          │ │
│ │ ☐ Reuse transcript from run #2 (skip STT)                   │ │
│ │                                    [ ▶ Start run ]          │ │
│ └─────────────────────────────────────────────────────────────┘ │
│ ┌ Runs (tick 2 to compare) ───────────────────────────────────┐ │
│ │ ☐ #3 large-v3 + Gemma QAT   done  STT 9.7s · LLM 41s   ★ ⋯ │ │
│ │ ☐ #2 turbo    + Gemma E4B   done  STT 4.2s · LLM 12s     ⋯ │ │
│ │ ☐ #1 turbo    (text only)   done  STT 4.2s               ⋯ │ │
│ │                                [ Compare selected (2) ]     │ │
│ └─────────────────────────────────────────────────────────────┘ │
│ ┌ Result · Run #3 ─────────────────────────────────────────────┐│
│ │ (Debrief)(Notes)(Checklist)(Diagram)(Speakers)(Tags)(Text)   ││
│ │ …tab content…                                                ││
│ └──────────────────────────────────────────────────────────────┘│
```

Why a **run** is the unit:
- STT and LLM are separate steps (memory budget may force sequential, see brief). A run = STT part + optional write-up part. "Reuse transcript from run #N" lets the user compare *only* LLMs without paying STT again (saves minutes and money-free but real time).
- **Rules for options:**
  - Model dropdowns show name, a one-line hint ("fast, 62% tech words") and RAM. Values come from server config **[A3]**, never hardcoded in the page.
  - "Start run" is disabled with a reason line if the model is not installed/loaded ("Qwen3.6 not available — start LM Studio").
  - Outputs that need speakers (Speakers) show a small note if the STT model has no timestamps **[A4]**.
- Presets are just saved dropdown values (localStorage per viewer). Reason: simple, no backend.

### D5. Output views (one tab per output)

Every result block carries a **footer line**: `Run #3 · Gemma 4 26B-A4B QAT · 41 s · 1,820 tokens`. Reason: user's goal is comparing; unlabelled output is useless.

Every tab has `[ Copy Markdown ]` and `[ Download .md ]`. Top of page also has `[ Copy all ]` / `[ Download all .md ]` (one file, all sections as `## headings`).

**Debrief** — 3 blocks, fixed order.
```
Debrief
 TLDR   Team agreed to deploy to VM today. No blockers.
 Decided  • Deploy at noon  • PR opens first
 Open     • Who owns KPI refresh bug?
```

**Notes** — headings + bullets. Plain text, selectable.

**Checklist** — tickable, with owner and due date when the model finds them.
```
 ☑ Open pull request                     Νίκος · today
 ☐ Deploy to VM                          Νίκος · today noon
 ☐ Send Excel with all rules             Μαρία · Fri
 3 items · 1 done            [Copy as - [ ] Markdown]
```
- Real `<input type=checkbox>` + `<label>` (keyboard: Space). Tick state saved per run **[A5: stored server-side or localStorage; backend decides]**. Copy Markdown writes `- [x]` / `- [ ]`.
- Items from the model are suggestions: each row has an edit (✎) and delete (✕). No add button in slice 1.

**Diagram** — Mermaid. Model writes Mermaid source; page renders to SVG.
```
 [ Flow ▾ | Mindmap | Sequence ]     [ Rendered | Source ]
 ┌────────────────────────────┐
 │  (rendered SVG)            │
 └────────────────────────────┘
 [Copy Mermaid] [Download .md (```mermaid block)] [Download SVG]
```
- If Mermaid fails to parse (small models do this often), show the **Source** tab with a red note "Diagram could not be drawn — model wrote invalid syntax" plus a **Retry** button. Never a blank card.

**Speakers (who talked)** — needs diarization **[A4]**; names are "Speaker 1/2/3" until the user renames.
```
 Talk time
 Speaker 1 (Ελένη)  ████████░░░░░░░░░░░░  38%  00:35
 Speaker 2 (Νίκος)  ████████████░░░░░░░░  58%  00:53
 Speaker 3          █░░░░░░░░░░░░░░░░░░░   4%  00:04
 Timeline  ▮▮▯▯▮▮▮▮▯▮▮▮▮▮▮▯   (click a block = jump audio)
 Turns
  [00:00] Speaker 1  Καλημέρα. Λοιπόν, γρήγορο standup…
  [00:08] Speaker 2  Εεε, τελείωσα το migration…
 Rename: [Speaker 1 → ______ ]
```
- Bars use colour + text %, never colour alone. Speaker colours reuse v1 `--s1/--s2/--s3` (already readable in dark mode).
- Clicking a timestamp seeks the player. Reason: the fastest way to verify a doubtful line.
- Warn on the tab: "Speaker labels are a guess. Check before sharing." (diarization is imperfect; the audio role should give numbers.)

**Tags** — chips: topics (`#postgres`), people, orgs, decisions. Click = copy. Export line: `tags: [postgres, blazor, deploy]` (front matter).

**Text (transcript)** — timestamps + speaker (if any), words the model was unsure about are underlined **[A6: needs confidence from STT; hide if not available]**.

**Export format (Markdown), one file:**
```
# Standup 3 Nov
_Run #3 · whisper-large-v3 + gemma-4-26b-a4b-qat · STT 9.7 s · LLM 41 s_
tags: [..]
## Debrief …  ## Notes …  ## Checklist (- [ ] …)  ## Speakers …
## Diagram  ```mermaid …```  ## Transcript …
```
Reason: model names + timings in the file make it self-explaining later.

### D6. Model comparison UX

Entry: tick 2 runs in the Runs list → **Compare selected**. Only 2 at a time (3+ is unreadable on a laptop; phone cannot show more than 1).

```
┌ Compare  #2 turbo+E4B   vs   #3 large-v3+QAT        [Swap] [Close] ┐
│ Show: (Transcript diff) (Debrief) (Notes) (Checklist) (Speakers)    │
│ ┌ A · Whisper turbo · 4.2 s ────┬ B · Whisper large-v3 · 9.7 s ────┐│
│ │ …το Γκέμα και το n8 ν…        │ …το Gemma και το n8n…            ││
│ │ Words changed: 14 of 210 (7%) │ ░ = different from the other     ││
│ └───────────────────────────────┴──────────────────────────────────┘│
│ Speed: A 4.2 s ■■    B 9.7 s ■■■■■   (audio 77 s → 18× / 8× real-time)│
└─────────────────────────────────────────────────────────────────────┘
Phone (<700px): panes stack; a toggle (A | B) switches; diff marks stay.
```

- **Transcript diff:** word-level diff, changed words highlighted with background **and** underline/strikethrough (not colour alone). Counter "N words differ". Computed in the page (small vanilla LCS) — no lib. **[A7]** Optional: if user pastes a reference text, show WER per run (v1 `eval/score.py` idea). Not in slice 1.
- **Text outputs (debrief, notes):** side by side, no diff (LLM text differs everywhere, a diff is noise). Instead, show *structure counts* under each: "5 bullets · 3 action items · 2 tags".
- **Checklist compare:** two lists, items present in only one are marked "only in A/B". Useful: reveals missed action items.
- **Header of each pane always shows:** run #, STT model, LLM model, time taken (STT + LLM separately), date. Fixed sticky header.
- **Fair-comparison guard:** if A and B used different STT (so the LLM saw different input), show a yellow line: "Different transcripts — LLM differences may come from STT." Reason: avoids wrong conclusions.
- **Run list keeps everything**: nothing auto-deleted. Per run: ★ (mark best), note field ("QAT better on names"), delete. Reason: the whole point is comparing over time.
- Timing source is the server (wall clock for each stage) **[A8]**. Label "wall time on this Mac, estimate of model speed, other apps affect it".

### D7. Progress and waiting UX

States of a run: `queued → loading model → transcribing → writing → done | failed | cancelled`.

```
┌ Run #4 · large-v3 + Gemma QAT ────────────────────────────────┐
│ ① Transcribing  ████████████░░░░░░░░  58%   elapsed 02:10     │
│ ② Write-up      waiting                                      │
│ Now: loading model… (first time takes longer)                 │
│ You can close this page. The job keeps running.               │
│                                    [ Cancel ]                 │
└───────────────────────────────────────────────────────────────┘
```

- **Real progress when possible:** STT % = audio seconds done / total. LLM has no known end: show tokens count + "elapsed", not a fake %. **[A9: server exposes stage + percent where known]**
- **ETA only as "estimate, not measured"** based on past runs of the same model on this machine (e.g. "usually ~8 min for 1 h"). Hidden if no history.
- **Partial results appear early:** transcript text shows as soon as STT finishes, while the LLM still works. Reason: user can start reading; also protects from losing STT work if LLM fails.
- **Cancel:** always visible; confirm dialog; result "cancelled" run is kept in the list (with partial transcript if any) so work is not lost.
- **Page closed / refresh:** job lives on the server. Reopen Analyse (or Library banner) → page reads job status by `rec` id and resumes the progress view. Page polls every 2 s (SSE is nicer but polling is simpler; backend decides) **[A10]**.
- **Optional browser notification** "Run #4 done" (opt-in button, uses `Notification` API) and tab title `⏳ 58% — detAIls` → `✔ Done`. Reason: 10-minute waits; user switches app.
- **Server restart during a job:** run shows `failed — server restarted` + [Retry]. No silent loss.
- **Memory warning before start:** if chosen STT + LLM exceed the budget at once, the page says "These will run one after the other (+~15 s model swap)" **[A11, estimate, not measured]**.
- **One job at a time** per Mac (memory). A second Start while busy = queued, shown as "queued (1 ahead)". Cancel works on queued runs too.
- Errors in plain words + a "Show details" fold (stderr text), never only a red toast. LM Studio not running → "Write-up model unreachable at <configured URL>. Start LM Studio." (URL comes from config, not hardcoded).

### D8. Look, accessibility

- Reuse v1 tokens: `--bg --card --fg --muted --line --good --warn --bad --accent`, light/dark via `prefers-color-scheme`, `.card`, `.tab`, pill buttons, max-width 820px (Compare widens to 1100px).
- One shared `shared.css` + `nav.js` for the 3 pages (avoids copy-paste drift).
- Keyboard: all controls native elements. Tabs use `role=tablist`, arrow keys switch (v1 tabs lack arrows — add). Visible `:focus-visible` outline.
- Contrast: `--muted #5f5f5f` on `#fafafa` is ≈6:1 (OK, estimate). Dark `#999` on `#1f1f1f` ≈ 5.5:1 (estimate, not measured). Check `--warn #d9a400` as text — it fails on white; use it only as a background/border with dark text.
- Status is text + icon, not colour only. Progress bar has `role=progressbar` + `aria-valuenow`. Live status uses `aria-live=polite`.
- Respect `prefers-reduced-motion` (no bar animation).
- Mobile: single column below 700px, buttons min 44px high.

### D9. One CDN lib: mermaid.js

- Needed only for Diagram. Drawing flow/mindmap by hand = hundreds of lines and bugs. Mermaid is the de-facto format LLMs know well, and the output is plain text (copy/paste into docs, GitHub renders it).
- Load it **lazily**, only when the Diagram tab opens. The user wants 100% local: **vendor the file** into `static/vendor/` (pinned version, no CDN at runtime) so no network request leaves the Mac. **Trade-off:** ~2–3 MB file in repo (size: estimate, not measured). Source: https://mermaid.js.org/ (check license MIT before vendoring).
- Fallback without it: the Source view still works.

## 3. Rejected options

| Option | Why not |
|---|---|
| One single page (record+analyse) | Record must stay light and stable during a meeting; analyse is heavy. v1 mic page already works. |
| File picker (upload) on Analyse | Library already knows the files; avoids path/privacy mistakes. Can be added later. |
| Compare 3+ runs | Unreadable at 820px; phone impossible. |
| Word diff for LLM text | Different wording everywhere = noise. Use structure counts + side by side. |
| Live streaming of LLM text token by token | Nice, but needs streaming API wiring; partial-by-stage is enough for slice 1. |
| Keep the page open to run job (browser-driven) | 1 h job + sleeping laptop/tab discard would lose it. Server must own jobs. |
| SPA framework / charts lib for talk time | CSS bars (`<div style="width:38%">`) are enough. |
| D3 / vis.js for diagrams | Heavier than mermaid, LLMs don't write that format. |
| Auto-delete old runs | User compares over time; storage is cheap versus re-running 10 min jobs. Offer manual delete. |

## 4. Risks and caveats that change a decision

- **Diarization (Speakers) may not exist** in the chosen STT stack **[A4]**. If not, Speakers tab = LLM guess from text (unreliable) or hidden. Decision needed from the STT/audio role; UI shows "unavailable for this model" meanwhile.
- **Small LLMs write broken Mermaid and malformed lists.** UI must tolerate: Source fallback + Retry (D5). Prompt role should ask for strict structured output (JSON) so checklist/tags render reliably.
- **Jobs need a server-owned queue and status API [A9, A10].** If the backend role picks something else (e.g. fire-and-forget), D7 "close the page" promise breaks. Needs agreement.
- **Timings are noisy** (swap, other apps, model first-load). Always label "wall time", show whether the model was cold-loaded **[A8]**, otherwise comparisons mislead.
- **Mermaid vendoring** adds a large file; if the user rejects it, ship Source-only diagrams.
- **Long transcripts (1 h) in the page:** a 10k-word diff in vanilla JS can freeze the tab (estimate, not measured). Run the diff in a Web Worker or limit to 5k words with "diff first N words". Test in slice.
- **Privacy:** a "Download .md" file includes names and content. Footer note in Export: "Contains the meeting content — not for git." Library lives outside git (brief rule).
- **Delete is permanent** (recordings include voices). Confirm dialog states exactly what is removed (audio + N runs).

## 5. Open questions for the user (max 5)

1. **Presets:** are two presets enough ("Cheap & fast" = turbo + E4B, "Best quality" = large-v3 + QAT)? Or do you want to name and save your own?
2. **Compare scope:** is 2 runs at a time OK, or do you need a 3-column table for a final shoot-out of STT models (numbers only, no text)?
3. **Speakers:** is "Speaker 1/2/3 + rename" acceptable, or do you need real names from the start (typed before the meeting)?
4. **Reference text:** do you want to paste a correct transcript to get a score (WER) per run? (v1 had `eval/score.py`.) Or compare by eye only?
5. **Notification when done:** browser notification + sound OK, or something else (Mac notification via script)?

## 6. Proposed OpenSpec change slices (UX angle)

Each is shippable alone and testable first (UI tests can be DOM-level with mocked API).

| # | Name | One line |
|---|---|---|
| 1 | `v2-shared-shell` | `shared.css`, nav bar, tokens, tab component with arrow keys. |
| 2 | `record-save-and-title` | Record page: title, timer, save card, link to Library (format per audio role). |
| 3 | `library-page` | List/search recordings, play, rename, delete-with-confirm, running-job banner. |
| 4 | `analyse-new-run-form` | Analyse page shell: header+player, model dropdowns from API, outputs chips, presets, Start. |
| 5 | `analyse-run-progress` | Job status view, stages, cancel, reopen-and-resume, tab title, notification. |
| 6 | `analyse-runs-list` | Keep all runs, labels (model+time), star, note, delete, "reuse transcript". |
| 7 | `output-transcript-notes-debrief` | Text/Notes/Debrief tabs, footer with model+time, copy/download .md. |
| 8 | `output-checklist` | Tickable checklist, edit/delete rows, copy as `- [ ]`. |
| 9 | `output-tags-speakers` | Tag chips; talk-time bars, turns list, rename, click-to-seek. |
| 10 | `output-diagram-mermaid` | Vendored mermaid, Rendered/Source toggle, invalid-syntax fallback + retry. |
| 11 | `compare-runs` | Pick 2 runs; header with model+time; transcript word diff; side-by-side outputs; fair-comparison guard. |
| 12 | `export-all-markdown` | One-file export with front-matter (models, timings, tags). |

Suggested order: 1 → 2 → 3 → 4 → 7 → 5 → 6 → 11 (STT compare first, it answers the user's main question) → 8 → 9 → 10 → 12.

## Assumptions list (for the other roles to confirm)

- A1 API: recordings list, runs list, models list (from config), job status.
- A2 Metadata (title, length, runs) comes from API.
- A3 Model catalogue incl. hint + RAM comes from server config.
- A4 Diarization/timestamps availability is known per STT model (flag in catalogue).
- A5 Checklist tick state persisted somewhere (backend or localStorage).
- A6 Word confidence optional.
- A7 WER scoring optional, later.
- A8 Server measures wall time per stage and cold/warm model load.
- A9 Job has stage + percent (where known) + elapsed.
- A10 Status by polling (2 s) is acceptable.
- A11 Sequential model loading ~15 s swap (estimate, not measured).
