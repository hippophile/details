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
