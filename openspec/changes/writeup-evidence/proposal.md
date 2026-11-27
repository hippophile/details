# writeup-evidence: every write-up line is one click from the moment it was said

## Driver
Owner, 2026-11-27 (backlog M7, M9, S5, S7, S8, N4): after a 3-hour meeting, a write-up line is only useful if you
can hear the moment it came from. Roast findings on `manos-filipGerman`:
- Quotes are plain text; they do not play the audio (`web/analyse/outputs.js` `itemNode`), though transcript rows do.
- "verified" only means the quote occurs in the transcript (`llm/quotes.py:20`). The worst transcript scored 33/33
  "verified", and the count line is hidden when everything is "verified" (`outputs.js` `renderKind`).
- Copy / Download .md drop every quote (`llm/markdown.py` never reads `quote`).
- Two write-ups of one recording are both "Analysis #1" (numbering is per transcript run, `api/analyses.py:176`).
- The live box always says "Gemma is writing" (`web/analyse/index.html`), even for Krikri.
- A failed write-up has no Retry on the Write-up card; its group Markdown answers "this analysis has no outputs yet".

## Depends on
Nothing unarchived. Implement after `stt-languages` only to avoid edit conflicts in `web/analyse/`.

## Decision
1. **Locate quotes in code, at read time.** New pure `quotes.locate(content, segments)` where `segments` is
   `[(start_ms, text)]` in order: for every dict with a `quote`, find `norm(quote)` in the normalised transcript
   (segments joined by one space, with an offset → segment map) and set `at_ms` to the start of the segment where
   the match begins. Not found → no `at_ms`. A quote spanning two segments resolves to the first. Runs without
   segments (v1 imports) get no `at_ms`. Nothing is stored; schemas are unchanged; old write-ups get it too.
2. **API.** `GET /api/analyses/{group_id}` and `GET /api/stt-runs/{id}/analyses` return each output's `content` with
   `at_ms` added (one segments query per transcript run, not per item).
3. **Page.** A quote with `at_ms` is a button `▶ mm:ss “quote”` (keyboard: Enter/Space) that calls the existing
   `AnalyseApi.seekTo` on the page player. Without `at_ms` it stays plain text. Tag pills with `at_ms` seek on click.
4. **Honest wording (M9).** The count line is always shown for a kind that has quotes: "N quotes found in the
   transcript, M not found (greyed out). Found means the words were said, not that the point is right." The JSON
   field stays `verified` (stored data, compare-runs and tests use it); only user-facing text changes. Markdown's
   `unverified` marker becomes `quote not found`.
5. **Markdown keeps evidence (S5).** After each item that has a quote, one indented line: `  > “quote” [mm:ss]`
   (`[mm:ss]` only when located). `render_output`/`render_group` receive located content. Golden files change.
6. **Numbering (S8).** `Analysis #n` counts all analysis groups of the **recording**, oldest first, so numbers are
   unique on the page. Positions still shift when one is deleted (same rule as runs).
7. **Live box (N4).** Title is "<model label> is writing", from the analysis being watched; "The model is writing"
   until the label is known.
8. **Failed write-up (S7).** The Write-up panel of a failed or cancelled kind shows the stored error and a **Retry**
   button (`POST /api/jobs/{job_id}/retry`, existing route). `GET /api/analyses/{group_id}/markdown` for a group
   without outputs answers 409 with the group's error when it has one, else today's message.

## Edited (expected; re-measure at task 1)
`llm/quotes.py`, `llm/markdown.py`, `api/analyses.py`, `web/analyse/{outputs.js,outputs.css,stream.js,index.html}`,
`tests/golden/*.md`, tests named in tasks. Not edited: schemas, prompts, pipeline, DB.

## Verification
- Red first: `locate` unit tests (found, not found, case/accents, spans two segments, repeated quote → first
  occurrence, no segments); API returns `at_ms`; golden Markdown with quotes; numbering across two transcript runs;
  409 with error; JS tests for the quote button and the count line; live title.
- Real: open `manos-filipGerman` in the browser, click three quotes, confirm the player jumps to the matching
  transcript line (screenshot); Download .md and show the quotes in the file.

## Caveats
- A short quote that occurs many times ("Ωραία!") jumps to its **first** occurrence, which may be the wrong one.
- "Found" is still not "supports the claim". Checking support needs a second model pass (backlog, not here).
- Labelled (speaker) analyses: a quote that includes the `Name:` prefix will not be located.
