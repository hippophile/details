# Tasks: writeup-evidence

## 1. Tests first (red; paste failures)
- [x] 1.0 Re-measure "Edited" with grep (who calls `render_output`, `render_group`, where `unverified` text appears,
      where `Analysis #` is built or asserted) and correct the proposal.
- [x] 1.1 `tests/test_llm_quotes.py`: `locate` cases from Verification.
- [x] 1.2 `tests/test_llm_api.py`: `at_ms` in both GET responses; numbering unique across two transcript runs of one
      recording; group Markdown 409 carries the error.
- [x] 1.3 `tests/test_llm_markdown.py` + `tests/golden/*`: quote lines with `[mm:ss]`, `quote not found`.
- [x] 1.4 `tests/js/outputs.test.mjs` (+ page test): quote button seeks; plain quote without `at_ms`; count line shown
      when 0 not found; Retry button on a failed kind; live title uses the model label (`stream.test.mjs`).

## 2. Implement (green)
- [x] 2.1 `quotes.locate`. 1.1 green.
- [x] 2.2 API (`at_ms`, numbering, 409). 1.2 green.
- [x] 2.3 Markdown + goldens. 1.3 green.
- [x] 2.4 Page: quote button, wording, Retry, live title. 1.4 green.
- [x] 2.5 `make test` once.

## 3. Prove on real data
- [x] 3.1 Browser check on `manos-filipGerman` as in Verification (needs the app restarted on this code). — done 2026-11-27: quote ▶ 04:56 jumped the player to 4:57 and lit the matching line; counts line shown

## 4. Close
- [x] 4.1 validate, tick, archive, commit.
