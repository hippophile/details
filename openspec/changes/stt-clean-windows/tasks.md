# Tasks: stt-clean-windows

## 1. Tests first (red)
- [x] 1.1 `tests/test_stt_clean.py`: junk dropped; loop collapsed to one unit, rest of line kept; 2-repeat drill line
      kept; end capped at duration; segment starting at/after duration dropped; out-of-order sorted; overlap trimmed
      (never to zero length); counts correct. Run it: fails (module missing). Paste the failure.
- [x] 1.2 `tests/test_stt_worker.py`: with `overlap_s=0.5`, fake engine calls show `[a−0.5, b+0.5]` clipped to
      `[0, duration]`; a segment whose midpoint is in window 1 is stored only by window 1; a spec without
      `overlap_s` gives the old window calls. Run: fails.
- [x] 1.3 `tests/test_stt_step.py`: a stored run has no junk segment and `raw.cleaned` counts. Run: fails.
- [x] 1.4 `tests/test_settings.py`: `stt.overlap_s` loads, env override works, negative rejected. Run: fails.

## 2. Implement (green)
- [x] 2.1 `src/details/stt_clean.py` (`JUNK`, `clean`); `live/worker.py` imports `JUNK`. 1.1 green; live tests green.
- [x] 2.2 `settings.py` + `config.toml`: `[stt] overlap_s = 3`. 1.4 green.
- [x] 2.3 `engines/worker.py`: overlap decode + midpoint ownership; `steps_stt.py` passes `overlap_s` in the spec.
      1.2 green. The fake engine may need to honour the asked bounds (keep its knobs working).
- [x] 2.4 `steps_stt.py`: `clean()` before insert, counts into `raw["cleaned"]`. 1.3 green.
- [x] 2.5 Full suite once: `make test`.

## 3. Prove on real data
- [x] 3.1 Re-run `manos-filipGerman` (turbo, auto). Report before/after counts and wall time (measured).
- [x] 3.2 List every dropped line of that run and read it; note any real speech lost in this proposal.
- [ ] 3.3 Screenshot the Analyse transcript of the new run around 00:28 and 02:58 (junk + loop spots).

## 3b. Correction after the real run
- [x] 3b.1 Red tests: join duplicate dropped (contained text, opposite sides of a join), paraphrase kept, same-side
      pair kept, rule off without window_s/overlap; "No, no, no, no, no!" kept, 8-repeat loop collapsed.
- [x] 3b.2 Implement in `clean()`; `steps_stt` passes window_s/overlap_s from the run settings. Green.
- [x] 3b.3 Re-run the analysis on `scratchpad/clean-proof/out/segments.jsonl`; update the Measured table; list every
      line dropped by the new rule and read it.

## 4. Close
- [ ] 4.1 `openspec validate stt-clean-windows`, tick tasks, archive, commit.
