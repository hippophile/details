# monday-brief: one screen that says what was decided, who disagreed, who owes what

## Driver
Owner, 2026-11-27: "you are in a meeting with 3 people, decisions and strategies, you disagree with one and the
other, 3 hours in. Next Monday you go and see just timestamps and words, that's useless." Backlog M4, M5, M6, M8,
S1, N3. Roast evidence on `manos-filipGerman` (a German verb drill): every model invented decisions, action items
and "high" risks, because the debrief prompt always asks for them; Gemma wrote Bengali and Vietnamese letters into
Greek words.

## Depends on
`writeup-evidence` (quote → `at_ms`, Markdown quote lines). Speaker names need `speakers-diarization` task 9.2 (owner's
Hugging Face token); without it the brief still works and `who`/`owner` are null unless a name is spoken.

## What the reader gets
```
BRIEF · meeting
Summary (3–6 sentences)
DECIDED            ✓ final decision                 ▶ 2:41:10
                     changed from: earlier decision ▶ 0:35:02
DISAGREED          ⚡ topic — still open | settled by: <decision>
                     Filip: stance ▶ 1:12:03 · Giannis: stance ▶ 2:05:40
TO DO              ☐ task (owner, due)              ▶ 2:50:12
OPEN QUESTIONS     ? question                       ▶ 1:40:55
CHAPTERS           0:00 Intro · 0:12 Pilot scope · 1:05 Budget
```
A lesson, interview or monologue shows "Nothing was decided." instead of invented items.

## Decision
1. **New output kind `brief`**, first in `KINDS` (so its tab is first and it is the tab shown on open). Schema
   `schemas/brief.v1.json`, prompt `prompts/brief/v1.md` (`checks: known_scripts`), setting
   `[llm] max_tokens_brief = 3000`. No DB change (`analysis_run.kind` and `output.kind` have no CHECK; verify in
   task 1.0).
2. **What the model writes (extract step).** `kind_of_recording` (`meeting | lesson | interview | other`),
   `summary`, `chapters[{title, quote}]` (quote = the first words of the chapter), `decisions[{text, quote}]`,
   `disagreements[{topic, positions[{who, stance, quote}]}]` (2+ positions), `action_items[{text, owner, due,
   done, quote}]`, `open_questions[{text, quote}]`. Prompt rules beyond the debrief's: decide the kind first; a
   decision is something the speakers **agreed to do or adopt**, not an opinion, a fact or a correct answer in an
   exercise; if the recording is not a meeting, `decisions`, `disagreements` and `action_items` are empty lists;
   `who`/`owner` is the speaker label at the start of the line or a name spoken in the transcript, else null.
3. **Code gives ids and order.** After extraction (single pass or merged map), code locates each quote
   (`quotes.locate`), sorts decisions, disagreements and chapters by position (unlocated last, original order), and
   assigns ids `d1..dn`, `x1..xn`.
4. **The model only links, code assembles (final decisions, M4).** One extra call, `brief_link`, when there are 2+
   decisions, or 1+ disagreement and 1+ decision. Input: the numbered decisions and disagreements with their times
   (no transcript). Output schema: `{decisions: [{id, replaces: [ids]}], disagreements: [{id, settled_by: id|null}]}`.
   Code validates: every id exists; `replaces` points only to **earlier** decisions; a decision is replaced at most
   once; no self-reference. Invalid → the usual single retry with the error text. Code then writes on each decision
   `status: "final" | "replaced"`, `replaces: [ids]`, `replaced_by: id|null`, and on each disagreement
   `settled_by: id|null`. The link call can never add, drop or reword an item.
5. **A failed link never loses the brief.** If the link reply is still invalid after its retry, or the call errors,
   the brief is stored with every decision `status: "final"`, no links, and `links: "failed"`; the page shows
   "Could not work out which decisions replaced earlier ones." (`links` is `"ok" | "skipped" | "failed"`.)
6. **Code-written fields** (`verified`, `id`, `status`, `replaces`, `replaced_by`, `settled_by`, `links`) are allowed
   in the stored schema and stripped from the schema sent to the model (`schemas._strip_verified` generalised to a
   named list; behaviour for the four existing kinds unchanged).
7. **Long meetings.** `merge("brief")`: lists concatenated in chunk order and de-duplicated on normalised `text`
   (`topic` for disagreements, merging their positions; `title` for consecutive chapters); `kind_of_recording` = the
   most common value (ties: first); `summary` through the existing reduce call. Then steps 3–4 run once on the
   merged result, so a decision in hour 1 can be replaced by one in hour 3.
8. **Check `known_scripts` (N3).** Every string the model writes outside `quote` must use only Greek, Latin, digits,
   punctuation and common symbols; otherwise a validation error naming the field and the foreign script, which
   triggers the existing single retry. Applies to prompt versions that list it (`brief` v1 now).
9. **Markdown** (`render_output("brief")`): `# Brief`, summary, `## Decided` (final decisions; each replaced one as
   an indented `changed from:` line under its replacement), `## Disagreed` (topic, `still open` or `settled by:
   <decision text>`, one indented line per position), `## To do` (`- [ ]` with owner/due), `## Open questions`,
   `## Chapters` (`- [mm:ss] title`). Empty Decided section renders "Nothing was decided." Quote lines as in
   `writeup-evidence`.
10. **Page.** A "Brief" chip (ticked by default) and tab; panel as in "What the reader gets", every quote and
    chapter a ▶ jump; empty lists say so ("Nothing was decided.", "No disagreements.", "Nothing to do.",
    "No open questions."). Prompts page and compare view list the kind (compare counts: decisions final/replaced,
    disagreements open/settled, action items, open questions, chapters, unverified). Brief action items are not
    tickable in this change.

## Not in this change
Asking questions of a meeting, carrying open items into the next meeting, rating a decision right/wrong, checking
that a quote supports its claim, one-click transcript + speakers + brief.

## Verification
- Red first, with the fake LM Studio: schema fixtures (good/bad), stripped model schema, id/order assignment,
  link validation (unknown id, forward reference, double replace, self), failed link → `links: "failed"` and brief
  kept, link skipped with one decision, merge across 3 chunks where chunk 3 replaces a chunk-1 decision,
  `known_scripts` (Bengali inside a Greek word → error; quote exempt), Markdown golden, API accepts `brief`, page and
  compare tests.
- Real, read by hand and reported honestly (needs the app restarted on this code and LM Studio):
  1. `manos-filipGerman` (15 min drill): expect `kind_of_recording: lesson` and empty decisions/disagreements/action
     items. This is the direct negative test of the roast.
  2. `giannis-vg-html` (2:08): map-reduce + link on a real long recording; count chunks, wall time, tokens; read the
     decisions and check five against the audio by clicking their quotes.
  Models: Gemma 4 12B and Krikri 8B. Results (including failures) go into this proposal.

## Caveats
- Small local models decide what counts as a decision. The schema and link step stop invented *structure*, not a
  wrong reading. The real runs above are the evidence; nothing here is claimed to work on a real meeting until then.
- Without speaker names, disagreements read "null: stance" and owners are mostly null (blocked on the owner's token).
- A 3-hour meeting is about 140k tokens in (estimate from 11.4k per 15 min measured): many chunk calls, tens of
  minutes on this Mac. Not measured.
- Chapters come from the model per chunk; long meetings may get too many or uneven chapters.

## Corrections after the independent audit (2026-11-27)
- **Segments with times.** `generate_kind` today receives text lines only. The step also loads the plain segments as
  `(start_ms, text)` and passes them as a new optional argument `timed_segments`; `quotes.locate` always runs on
  these plain segments (never on labelled `Name:` lines). Ordering and ids (decision 3) and the link input times
  (decision 4) use them. Unlocated items keep model order after located ones.
- **How the link call is stored.** It is NOT a separate attempt: `_persist` chains attempts with `retry_of` and the
  page judges the kind by the last one, so a failed link would hide a good brief. The link call(s) are folded into
  the kind's aggregate attempt like map-reduce calls: tokens and wall time summed; link prompt, reply and errors kept
  in `params.link` (visible in Inspect). In single-pass mode the single attempt becomes such an aggregate when a link
  ran. Link token limit: `[llm] max_tokens_brief_link = 800` (settings + config; validated like the others).
- **Errors.** Decision 5 catches `LLMError` and link validation failure only. `StreamCancelled` propagates, so
  Cancel stores nothing.
- **known_scripts never loses a brief over a name.** `who` and `owner` are exempt (a real name may be Cyrillic or
  Chinese). If the check still fails after the retry for other fields, the brief is stored with the offending
  strings kept and `script_warnings: [field paths]`; the page shows "Some text is in an unexpected alphabet."
  (The check is therefore not a schema-validity failure for `brief`; for any later prompt using it, the same rule.)
- **Link prompt is code, not a versioned prompt** (like `REDUCE_PROMPT` in `pipeline.py`).
- **Existing tests that must be edited, not only added** (measured by the audit): `test_llm_schemas.py:9`
  (`test_four_kinds_only`), `test_prompts_api.py:80,126`, `test_llm_page.py:12`, the inline TOML in
  `test_settings.py`, and `for kind in schemas.KINDS` loops that need a `brief` fixture (`test_llm_pipeline.py:22`).
- **Files the decision list missed:** chip in `web/analyse/index.html`; `compare.js` `TABS` and its per-kind branches;
  `comparison.py` loops `KINDS`, so its response gains a `brief` key.
- **Cost:** the default analysis now runs five kinds instead of four: slower. Time measured in task 3.1.

## Measured

### Task 1.0: where the four kinds are hard-coded (grep, 2026-11-27)
- Source, kind list: `schemas.KINDS` (1, everything else imports it: `markdown`, `prompts`, `api/analyses`, `api/prompts`,
  `comparison`); literal lists in web: `outputs.js` KINDS + TITLES, `compare.js` TABS, `prompts.js` TITLES (4 lines);
  `index.html` 4 chips.
- Source, per-kind branches: `pipeline.merge` 4, `pipeline.REDUCE` 2 entries, `markdown.render_output` 4,
  `comparison.output_counts` 4, `compare.js` `countsText` + `content` (the final `else` is debrief), `outputs.js`
  `renderKind` (the final `else` is debrief).
- Settings: `max_tokens_<kind>` 4 fields + the `_v_llm` tuple in `settings.py`, 4 lines in `config.toml`, 4 in the inline
  TOML of `test_settings.py`. `Llm.max_tokens(kind)` is `getattr`, so `max_tokens_brief_link` needs no new code path.
- Tests asserting the list: `test_llm_schemas.py` (2), `test_llm_page.py` (1), `test_llm_markdown.py` (1),
  `test_compare_counts.py` (1), `test_llm_steps.py` KINDS (1), `test_prompts_api.py` (3: stored rows, kinds set,
  folder list), `test_settings.py` inline TOML. `test_llm_pipeline.py` / `test_llm_prompts.py` loop over `KINDS` and need
  a `brief` fixture / file only. The 422 test (`test_llm_api.py`) does not assert the message text.
- DB: 0 CHECK constraints on `kind` (`job.kind`, `analysis_run.kind`, `output.kind`, prompt kind are all plain
  `text NOT NULL`, db/migrations 0003 and 0005). No migration needed.

### Task 3.1: real runs, 2026-11-27 (no app server; `pipeline.generate_kind` called directly)
Script and outputs: `scratchpad/brief-proof/` (`run_brief.py`, `*.brief.json`, `*.brief.md`, `*.run.json`, logs). Prompt body
from `prompts/brief/v1.md`, settings from the repo, segments read with SELECT only (read-only transaction). LM Studio had
nothing loaded before; each model was loaded once under the app's identifier (`details-gemma-12b`, `details-krikri-8b`,
16384 context, as in `models.toml`) and unloaded afterwards. Free+inactive RAM before loading: 12.7 GB (needs 10) and 17.5 GB (needs 8).

| Run | Result |
|---|---|
| `manos-filipGerman` (15.3 min, whisper-large, 402 segments), Gemma 4 12B | `kind_of_recording: lesson` ✓. decisions 0 ✓, action items 0 ✓, open questions 0, **disagreements 1 ✗ (invented)**. 92.8 s, 2 calls (9,391 in / 944 out). Attempt 1 was rejected by `known_scripts` ("summary: contains Bengali letters"), the retry fixed it: this is the roast failure, caught live. Chapters 5, one with a quote not found (no time). Summary Greek with a few garbled words (`ασχονούνται`, `κλίινση`), all Greek letters. |
| same, Krikri 8B | `lesson` ✓ but **2 decisions (one "replaced" by the other), 1 action item, 2 open questions invented ✗**. 38.2 s, 2 calls incl. the link call (3,701 in / 443 out). Link ran because it saw 2 decisions: it replaced the model's own invention. |
| `giannis-vg-html` (128.2 min = 2 h 08, **whisper-turbo: it has no whisper-large run**, 2338 segments), Gemma 4 12B | map-reduce, **4 chunks**, calls: 4 chunk + 1 reduce + 1 link = 6, **411.7 s (6.9 min)**, **33,031 tokens in / 2,782 out**. `meeting`, `links: ok`, decisions 9 (9 final, **0 replaced**; the link call said "no replaces"), disagreements 2 (both open), action items 5, open questions 5, chapters 19, `script_warnings` none. Quotes found 31, not found 11. |

Five decisions of the Giannis brief to click-check (time is where the quote is first found; all five quotes were found):
1. 6:08 "Η παρουσίαση πρέπει να επικεντρώνεται στην αφήγηση μιας ιστορίας." ← "Για να πω μια ιστορία δεν πρέπει να με οδηγείς."
2. 32:00 "Αποφασίστηκε η αποφυγή scroll στις διαφάνειες." ← "No scrolls." (short quote; first occurrence may be an earlier one)
3. 43:32 "Αποφασίστηκε η χρήση της ορολογίας 'Usage' αντί για 'Uses'." ← "Το uses, είναι λάθος αν είναι. Usage είναι."
4. 1:11:36 "Χρήση πραγματικών δεδομένων αντί για mock data." ← "Βάλε data, δεν ξέρω τον ένωσο, θέλω να μου λέμε θεωρετικά"
5. 2:05:30 "Χρήση HTML για την παρουσίαση των δεδομένων." ← "Ο στόχος είναι να αντικαταστήσουμε το PowerPoint μας με κάτι άλλο physical, το οποίο λέγεται HTML"

What the real runs show (honest):
- The negative test is **not passed**: both models call the drill a `lesson` but still invent items (Gemma 1 disagreement, Krikri
  2 decisions + 1 action item + 2 questions). The schema and the link step stop invented structure only; they cannot stop a
  model from reading a correction as a decision. Prompt wording is the lever, not code: a later `prompts/brief/v2.md`.
- Invented speaker names: Gemma wrote `who: "Speaker 1"` / `"Speaker 2"` in the Giannis brief although the prompt says null unless
  spoken, and one position quote is English text that is not in the transcript ("I think usage is the most important."). Nothing
  here checks `who` against the transcript.
- The link call is cheap (6.6 s, 529 in / 41 out on 9 decisions) but found no replacement in 2 h of design talk; whether that
  is right was not checked against the audio. The "replaced" path was exercised for real only by Krikri on the invented decisions.
- Time cost: the Giannis brief alone took 6.9 min on top of the other four kinds (measured above for this one kind only).
- Short quotes ("No scrolls.", "Δεν είναι") resolve to their first occurrence in the recording, which can be the wrong place.

### Corrections made while implementing
- `giannis-vg-html` is 2 h 08, not 2:08 minutes, and has only a whisper-turbo run; the proof used it (largest available).
- Decision 5 says "twice" for an LM Studio error during the link; like every other call, `LLMError` is not retried (only
  invalid replies are), so one error ends the link and stores `links: "failed"`. Tokens of a reply already received before an
  error are not counted in the folded attempt.
- A position with no speaker is shown as "Unknown speaker: stance" (page and Markdown), not "null: stance" as the Caveats line said.
- Markdown format chosen: `- decision` + quote line + nested `- changed from: earlier [mm:ss]` (a chain nests deeper); disagreement
  `- topic (still open)` or `(settled by: decision text)` with one nested line per position; empty chapters read "No chapters."; a failed
  link or script warnings add one italic line (the export would otherwise drop them silently).

## Correction after the real runs (orchestrator, 2026-11-27): enforce in code what the model was only asked
Both models labelled the German drill `lesson` and still returned decisions / a disagreement / action items; Gemma
wrote `who: "Speaker 1"` with no speaker labels in the input. The prompt rules are not enough, so code enforces them
after extraction (before ids and link):
1. `kind_of_recording != "meeting"` → `decisions`, `disagreements`, `action_items` are emptied; their counts go to
   `dropped_not_meeting: {decisions: n, disagreements: n, action_items: n}` (shown in Inspect, one line on the page:
   "Not a meeting: N items the model proposed were left out.").
2. `who` / `owner` are kept only if the name occurs in the transcript text (`quotes.norm` containment) or is one of
   the speaker names of a labelled run; otherwise set to null and counted in `names_cleared`.
3. A disagreement keeps only positions whose quote was found; with fewer than 2 found positions it is dropped and
   counted in `dropped_unsupported`. (Decisions and items keep the existing rule: kept, greyed when not found.)
Re-run proof: the two `manos-filipGerman` briefs re-processed from their saved model replies (no new model call)
must have 0 decisions / disagreements / action items; the giannis brief re-processed must lose only the
`Speaker 1/2` names and unsupported positions — list what changed.

### Task 3b: re-processed from the saved briefs (no model call; `scratchpad/brief-proof/reprocess.py`)
Input is the saved stored brief (not the raw reply, which the proof script did not keep); `_enforce_brief` is idempotent on it.
- manos Gemma: disagreements 1 → 0 (`dropped_not_meeting` 0/1/0); decisions and action items 0 as before.
- manos Krikri: decisions 2 → 0, action items 1 → 0 (`dropped_not_meeting` 2/0/1). Open questions (2) are not covered by rule 1 and stay.
- giannis Gemma: disagreements 2 → 1 (`dropped_unsupported` 1: the `Speaker 1/2` one, whose second quote was not found, so the names left with it, `names_cleared` 0). The rest unchanged: 9 decisions, 5 action items, 5 questions, 19 chapters.
