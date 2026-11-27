# Backlog: the Monday Brief

Driver (2026-11-27): "3 people, 3 hours, decisions and strategies, people disagree. Next Monday you see just
timestamps and words, that's useless." Goal: one screen that answers *what did we decide, who disagreed, what do
I owe, what is still open*, with every line one click from the moment it was said.

Source: roast of `manos-filipGerman` (15:19, 3 STT runs, 4 analyses) by two independent reviewers, 2026-11-27.

## Status key
- **NOW**: can be built and tested today, no outside input needed.
- **BLOCKED**: needs something from the owner first (named).
- **PARTIAL**: can be built now, but only proven once the blocker is gone.

## MUST
| # | Item | Status | Blocker / note | Change |
|---|---|---|---|---|
| M1 | Clean transcript: drop Whisper junk ("Υπότιτλοι AUTHORWAVE", silence lines, repeat loops), overlap the 30 s windows, cap end times, order segments | NOW | — | `stt-clean-windows` |
| M2 | Languages: allowed set (e.g. el+en+de), re-decode a window detected outside it; run label shows the real setting (run #3 was `auto` but is stored as `el`) | NOW | — | `stt-languages` |
| M3 | Who said it: speaker names on every line | BLOCKED | Owner: HF read token in `.env` + accept model terms, then `make diar-model`. pyannote already installed. Tasks 9.2, 12.x of `speakers-diarization`; 12.3 needs a second real person | `speakers-diarization` |
| M4 | Final decisions: track a decision across a long meeting; latest wins, earlier shown as "changed from" | PARTIAL | Buildable now (map-reduce step + schema). Proof needs a real long meeting with decisions | `monday-brief` |
| M5 | Disagreements: topic → who held which position → resolved / open | PARTIAL | Needs M3 for names; without it only "Speaker 1/2" | `monday-brief` |
| M6 | Action items with owner + deadline | PARTIAL | Schema already has owner/due; owner needs M3 | `monday-brief` |
| M7 | ▶ on every write-up line: quote → jump audio to that moment | NOW | — | `writeup-evidence` |
| M8 | Write-ups may say "none"; recording type (meeting / lesson / interview) so nothing is invented | NOW | — | `monday-brief` |
| M9 | Rename "verified" to "quote found" (it only checks the quote exists, `llm/quotes.py:20`) | NOW | — | `writeup-evidence` |

## SHOULD
| # | Item | Status | Note |
|---|---|---|---|
| S1 | Chapters: 3 h split into topics with timestamps | NOW | |
| S2 | Ask the meeting: question → answer with ▶ timestamps | NOW | Long transcripts need retrieval; pgvector fits the stack |
| S3 | Carry-over: open questions + action items roll into the next meeting's brief | PARTIAL | Needs a "series" (which meetings belong together) |
| S4 | Monday Brief opens first; transcript one click away; controls collapsible | NOW | |
| S5 | Copy / Download .md keeps quotes (`llm/markdown.py` drops them) | NOW | |
| S6 | Open on the best run (large over turbo); language in run label | NOW | |
| S7 | Failed write-up shows real reason + Retry | NOW | |
| S8 | Write-up numbering once per recording (two "Analysis #1" today) | NOW | |
| S9 | Who talked card: setup text → "Set up" link | NOW | |
| S10 | A real long test meeting (30–60 min, 3 people, a disagreement) to measure against | BLOCKED | Owner records or picks one; `giannis-vg-html` (2:08) is the closest in the Library |

## NICE
| # | Item | Status |
|---|---|---|
| N1 | Join 2-second lines into sentences | NOW |
| N2 | "2:12 · 7.0×" → "took 2 min · 7× faster than real time" | NOW |
| N3 | Flag write-ups with letters from other alphabets (Gemma: Bengali, Vietnamese); dedupe tags | NOW |
| N4 | Live box names the real model, not always "Gemma" (`web/analyse/index.html:97`) | NOW |
| N5 | Follow-up email draft (decisions + who owes what) | PARTIAL (M3) |
| N6 | Talk-time bar per chapter | PARTIAL (M3) |
| N7 | Mark a decision right/wrong so the next write-up learns | NOW |

## Estimates for a 3-hour meeting (from this recording's measured speed, not measured on 3 h)
- Transcript: turbo 7.0× → ~26 min; large 3.4–4.7× → ~38–53 min.
- Write-up: 15 min ≈ 11.4k tokens in, so 3 h ≈ 140k tokens; local models read it in chunks, likely tens of minutes.

## Specced 2026-11-27 (implementation order; each depends on the one before only to avoid edit conflicts)
| # | Change | Covers |
|---|---|---|
| 1 | `stt-clean-windows` | M1 |
| 2 | `stt-languages` | M2, S6 |
| 3 | `writeup-evidence` | M7, M9, S5, S7, S8, N4 |
| 4 | `monday-brief` | M4, M5, M6, M8, S1, N3 |
| 5 | `analyse-review-layout` | S4, S9, N1 |

## Not specced yet, and why
| Item | Why not now |
|---|---|
| M3 speakers | Owner's Hugging Face token (see `speakers-diarization` task 9.2) |
| S2 Ask the meeting | Needs a design choice first (search over 3 h of text: Postgres full-text vs pgvector + an embedding model) and should sit on top of the Brief |
| S3 Carry-over | Needs a way to say which meetings belong together |
| S10 Real long test meeting | Owner records or picks one |
| N2 Friendlier run label wording | Wording decision; the label now gains the language (S6) |
| N5 Follow-up email, N6 talk-time per chapter | Need speaker names (M3) |
| N7 Mark a decision right/wrong | Only useful once the Brief is proven on real meetings |
| One click: transcript + speakers + brief | Owner expected it (noted 2026-11-17); spec after the Brief exists |
| Check that a quote supports its claim | Second model pass; cost and accuracy unknown |
