# copy-transcript: Copy button for the whole transcript on the Analyse page

## Driver
User, 2026-11-13: "i want it to be able at this phase to copy paste the whole transcript".

## Today
`web/analyse/analyse.js` `select()` draws one clickable `.seg` row per segment (`[mm:ss]` + text, optional speaker chip). There is no copy control; select-all also copies the stamps and a click on a row seeks the player.

## Decision
1. A **Copy transcript** button and a **Timestamps** tick-box (off by default) in the Transcript card header.
2. Pure helper `AnalyseApi.transcriptText(segs, {timestamps})` in `web/analyse/api.js`: one line per segment, `[mm:ss] ` prefix only when asked, `Name: ` prefix when the segment has a speaker. Lines joined with `\n`.
3. Read-only: nothing is written to the database or the recording.
4. Runs imported from v1 (no segments) copy `full.text`.
5. Clipboard failure shows "Copy failed: <reason>" in the page status; success shows "Copied."

## Edited
`web/analyse/{index.html,api.js,analyse.js,analyse.css}`. New: `tests/js/copy-transcript.test.mjs`, `tests/test_copy_transcript_js.py`.
