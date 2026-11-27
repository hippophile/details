# Tasks: writeup-evidence

## 1. Tests first (red; paste failures)
- [ ] 1.0 Re-measure "Edited" with grep (who calls `render_output`, `render_group`, where `unverified` text appears,
      where `Analysis #` is built or asserted) and correct the proposal.
- [ ] 1.1 `tests/test_llm_quotes.py`: `locate` cases from Verification.
- [ ] 1.2 `tests/test_llm_api.py`: `at_ms` in both GET responses; numbering unique across two transcript runs of one
      recording; group Markdown 409 carries the error.
- [ ] 1.3 `tests/test_llm_markdown.py` + `tests/golden/*`: quote lines with `[mm:ss]`, `quote not found`.
- [ ] 1.4 `tests/js/outputs.test.mjs` (+ page test): quote button seeks; plain quote without `at_ms`; count line shown
      when 0 not found; Retry button on a failed kind; live title uses the model label (`stream.test.mjs`).

## 2. Implement (green)
- [ ] 2.1 `quotes.locate`. 1.1 green.
- [ ] 2.2 API (`at_ms`, numbering, 409). 1.2 green.
- [ ] 2.3 Markdown + goldens. 1.3 green.
- [ ] 2.4 Page: quote button, wording, Retry, live title. 1.4 green.
- [ ] 2.5 `make test` once.

## 3. Prove on real data
- [ ] 3.1 Browser check on `manos-filipGerman` as in Verification (needs the app restarted on this code).

## 4. Close
- [ ] 4.1 validate, tick, archive, commit.
