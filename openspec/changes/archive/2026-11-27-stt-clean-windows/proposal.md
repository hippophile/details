# stt-clean-windows: a transcript the write-up can trust

## Driver
Owner, 2026-11-27, after a 3-hour-meeting thought experiment: on Monday "just timestamps and words, that's useless".
Every write-up (debrief, decisions, owners) is built from the transcript, so this is backlog item M1
(`openspec/backlog.md`) and goes first. Example of the harm: on `manos-filipGerman` run #3, the Whisper junk line
"Υπότιτλοι AUTHORWAVE" became the meeting's topic ("σύστημα υποτίτλων") and checklist items, all marked verified.

## Today
- `engines/worker.py` cuts the audio into hard 30 s windows (`config.toml [stt] window_s`), no overlap; each window is
  decoded alone (`condition_on_previous_text` is off, `runner/steps_stt.py:45`). Words are cut at the joins.
- `engines/mlx_whisper.py` keeps every non-empty segment. The junk-phrase list exists only in the live worker
  (`live/worker.py:15`).
- Segment end times are not capped at the audio length, and segments are not ordered/trimmed across windows.

## Measured blast radius (2026-11-27, all 11 saved runs, 7,752 segments, `~/details-data/jobs/*/stt_transcribe/`)
| problem | count |
|---|---|
| known junk phrase ("υπότιτλοι", "authorwave", "subtitles") | 106 |
| repeat loop (a 1–4 word unit repeated 5+ times in a row in one segment) | 12 |
| segment ends after the audio ends | 9 |
| segment starts before the previous one ends | 169 |
| Whisper "silence" rule (no_speech_prob ≥ 0.6 and avg_logprob ≤ −1.0) | 0 → **not added** |

## Decision
1. New pure module `src/details/stt_clean.py`:
   - `JUNK` phrase list moves here; `live/worker.py` imports it (same list, same live behaviour).
   - `clean(segments, duration_ms) -> (segments, counts)`: drops junk-phrase segments; collapses a repeat loop to one
     copy of the unit (keeps the rest of the line); drops a segment left empty; caps `end_ms` at `duration_ms`;
     drops segments starting at/after `duration_ms`; sorts by `start_ms`; trims `end_ms` so it is ≤ the next
     segment's `start_ms` (never below its own `start_ms + 1`).
   - `counts = {"junk": n, "loops": n, "capped": n, "trimmed": n}`.
2. Overlap windows: `[stt] overlap_s = 3` (config, env `DETAILS_STT_OVERLAP_S`). The worker decodes
   `[a − overlap, b + overlap]` (clipped to the audio) and keeps only segments whose midpoint lies in `[a, b)`
   (the last window keeps up to the end). The spec carries `overlap_s`; a resumed job whose spec has no `overlap_s`
   runs with 0 (old behaviour, so old checkpoints stay valid).
3. `steps_stt.stt_transcribe` runs `clean()` on the collected segments before storing them and stores the counts in
   `stt_run.raw["cleaned"]` (visible in Inspect). Nothing is dropped silently.
4. Only new runs change. Old transcripts stay as they are; re-run a recording to get a cleaned one.

## Not in this change
Allowed languages and the wrong run language label (run #3 was `auto`, stored as `el`): `stt-languages` (M2).

## Verification
- Unit (red first): `clean()` on each problem above, plus negatives: a drill line "Gehelfen Gehelfen" (2 repeats)
  and 4 separate "Gehelfen" segments are kept; a real line containing the word "subtitles" inside a longer sentence
  is a known false positive and is documented, not hidden.
- Worker with the fake engine: overlap windows are requested with the right bounds; a segment straddling a join is
  stored once; a resumed old spec without `overlap_s` behaves as before.
- Real: re-run `manos-filipGerman` with turbo + auto. Before → after: junk lines (7 → 0), loops, segments past
  919.8 s, overlapping segments (expect 0 for the last three). Read the dropped lines by hand to check no real
  speech was lost. Wall time before/after reported (overlap adds ~20% audio: estimate, then measured).

## Caveats
- Overlap costs time: 3 s on each side of a 30 s window ≈ +20% audio decoded (estimate).
- The junk list is a phrase list: it will miss new Whisper hallucinations and could drop a real line that contains
  one of the phrases. Counts in Inspect make both visible.
- This does not fix wrong words, only made-up and broken lines. Accuracy on mixed Greek/German is M2's job.

## Corrections after the independent audit (2026-11-27)
- **overlap_s is a run setting, not a server setting.** `steps_stt` rewrites `spec.json` on every step run, so taking
  `overlap_s` from current settings would give a job started before this change overlap on top of hard-cut
  checkpoints. `start_run` stores `overlap_s` in `stt_run.settings` (from `[stt] overlap_s`); the step reads
  `rs.get("overlap_s", 0)`. A run row without it decodes with no overlap.
- **"Stored once" is a timing rule, not a text guarantee.** Two windows can split the same speech differently, so a
  phrase can still appear twice near a join, or lose words. Task 3.1 also counts phrases repeated at joins (same
  3+ word sequence in consecutive segments within ±3 s of a join), old vs new.
- Fake engine: a final window shorter than `overlap_s` loses its fake segment; tests use a last window ≥ `overlap_s`.

## Measured (2026-11-27, `manos-filipGerman` audio, turbo, auto, 919.8 s; worker run outside the app, overlap_s = 3)
Old = run fb8195bc as stored (raw, then `clean()`); new = fresh worker output (raw, then `clean()`).
| | old raw | old cleaned | new raw | new cleaned |
|---|---|---|---|---|
| segments | 396 | 394 | 389 | 389 |
| junk-phrase lines | 2 | 0 | 0 | 0 |
| repeat loops | 1 | 0 | 2 | 0 |
| segments ending after the audio | 1 | 0 | 1 | 0 |
| overlapping segments | 13 | 0 | 9 | 2 (two pairs with the same start: kept 1 ms long by the "never below start+1" rule) |
| phrases repeated at a join (3+ words, within 3 s) | 2 | 2 | 5 | 5 |
Wall time (model load + decode): old 131.7 s, new 141 s (+7%, measured once; a few short test runs overlapped it).
Not the 7 junk lines of the 11-run survey: this run had 2 and the new decode produced none (Whisper output differs per run).
Dropped/altered lines read by hand (`dropped_old.txt`, `dropped_new.txt` in the session scratchpad `clean-proof/`):
- Real speech lost: "No, no, no, no, no!" (452 s) became "No," (5 repeats = loop threshold). A false positive of the 5+ rule.
- Overlap does not remove join repeats: 2 -> 5 (e.g. "If there is B" at two adjacent segments). Midpoint ownership decides
  who keeps a segment, not what the text says (see caveat above). Nothing in `clean()` merges these.
- Everything else dropped was junk or a real loop ("Αφικού" x50, "να γίνει" x60).
- Not done: 3.3 browser screenshot (needs the owner's app restarted with the new code).

## Correction after the real run (2026-11-27, orchestrator)
The measured run showed two problems, so `clean()` gains two rules before archive:
1. **Join duplicates.** Overlap raised repeated phrases at window joins from 2 to 5 (e.g. "I'm not kidding, I'm not
   kidding" ‖ "I'm not kidding"; "I was mad." ‖ "…because I was mad."). `clean(segments, duration_ms, window_s=None,
   overlap_s=0)`: for two consecutive segments that lie on opposite sides of a window join (join = k·window_s) and
   both start within `overlap_s` + 2 s of it, if the normalised text of one (`quotes.norm`) is contained in the
   other's, drop the shorter (ties: keep the earlier). Count `join_dupes`. Without `window_s` or with `overlap_s = 0`
   the rule is off. Not merged: paraphrases ("I don't know what you mean." ‖ "You know what you mean.").
2. **Loop threshold 5 → 8.** "No, no, no, no, no!" (real speech) was collapsed. A unit must repeat 8+ times in a row.
Task 3.1 is re-run on the saved new windows (no new GPU run needed) and the table updated.

### Measured after the correction (same saved windows, no new GPU run; `clean(.., window_s=30, overlap_s=3)`)
| new run | raw | cleaned |
|---|---|---|
| segments | 389 | 387 |
| junk / loops (8+) / past audio | 0 / 1 / 1 | 0 / 0 / 0 |
| overlapping | 9 | 2 (same-start pairs, 1 ms) |
| join dupes dropped | | 2 |
| 3-word phrases repeated near a join | 5 | 5 |
- "No, no, no, no, no!" is kept now (threshold 8). Only real loop collapsed: "να γίνει" x60.
- Lines dropped by the join rule (read): 61.0 s "Γετρύγκτ" (kept 59.0 s and 63.0 s copies) and 691.0 s "Γεγέσεν" (kept 689.0 s).
  Single-word, unverifiable by ear: if the speaker really said it twice, one copy is lost.
- The 5 three-word repeats ("I'm not kidding, I'm not kidding" || "I'm not kidding", "I was mad." || "...because I was mad.", ...) are NOT
  join duplicates: both segments sit on the same side of a join (e.g. 743, 745, 747 s; join at 750 s), so they are repeats inside one
  window and the join rule leaves them. Decision for the orchestrator: widen the rule to same-side pairs or accept.
- Rule detail added: one drop per pair (the survivor is not compared with the next segment), else three "Γετρύγκτ" in a row collapsed to one.
