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
