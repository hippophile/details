# analyse-review-layout: the write-up and the transcript come first

## Driver
Owner, 2026-11-27 (backlog S4, S9, N1). Opening a recording to review it shows, in order: player, New run, AI model,
Write-up, Runs, Who talked, Transcript. The transcript is the last card and is a list of 2-second lines. The
"Who talked" card shows a paragraph of setup instructions while the speaker model is missing.

## Depends on
`monday-brief` (edits the same page files). Implement last.

## Decision
1. **Card order** in `web/analyse/index.html` (ids, contents and scripts unchanged): Recording → Running → live box
   → insights host → **Write-up** → **Transcript** → New run → AI model → Runs → Compare → Who talked.
2. **Paragraph view.** Pure helper `AnalyseApi.paragraphs(segs, opts)` groups consecutive segments. A new paragraph
   starts when the speaker changes, when the silence before a segment is longer than `gapMs` (default 1500), when
   the paragraph is at least `minMs` long (default 8000) and the previous segment ends with `. ; ! ? …`, or when it
   reaches `maxMs` (default 45000). Each paragraph row shows `[mm:ss]`, the speaker chip if any, then the segments
   as inline spans; each span still seeks to its own segment on click/Enter and is highlighted while it plays.
3. **Toggle** "Paragraphs / Lines" in the Transcript card header, default Paragraphs; the choice is kept in
   `localStorage`. Lines = today's view, unchanged. Copy transcript is unchanged (one line per segment).
4. **Who talked, model missing.** One line "Speaker detection is not set up." and a closed
   `<details><summary>How to set it up</summary>` holding the API's reason and hint text. The Detect button stays
   disabled as today.

## Verification
- Red first: node tests for `paragraphs` (speaker change, gap, sentence end after `minMs`, `maxMs`, empty, one
  segment); page tests for card order, the toggle and its default, the collapsed setup text; existing transcript
  click-to-seek and copy tests stay green untouched.
- Real: screenshot of `manos-filipGerman` at the top of the page (Write-up then Transcript visible) and of the
  paragraph view; click one span and confirm the player time.

## Caveats
- Whisper punctuation is unreliable, so paragraph breaks are approximate; Lines view remains for exact work.
- The default view of the transcript changes for the owner; one click returns to Lines and it is remembered.

## Notes from the independent audit (2026-11-27)
- `tests/e2e/test_diar_brave.py:118` reads `#spkReason` innerText; text inside a closed `<details>` is not rendered,
  so that e2e assertion must be updated (keep `#spkReason` as the one-line notice, put the steps in the disclosure).
- Paragraph spans need their own playing-highlight CSS rule (`.seg.on` only styles rows); `segEls` must hold the spans.
- With New run / AI model below the transcript, starting the first run means scrolling past it: when a recording has
  no done run, those two cards stay above the transcript.
