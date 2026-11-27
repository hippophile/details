# Tasks: monday-brief

## 1. Tests first (red; paste failures)
- [ ] 1.0 Re-measure with grep: every place the four kinds are listed (`KINDS`, `max_tokens_*`, `outputs.js`,
      `prompts.js`, `compare.js`, `comparison.output_counts`, `markdown.render_output`, tests that assert the kind
      list or a 422 message) and any DB CHECK on `kind`. Write the counts into the proposal.
- [ ] 1.1 `tests/test_llm_schemas.py`: `brief.v1` good/bad fixtures; model schema has none of the code-written fields;
      the four existing kinds' model schemas are byte-identical to before.
- [ ] 1.2 `tests/test_llm_pipeline.py` (fake chat): ids and order; link applied; each invalid-link case retried once;
      failed link keeps the brief with `links: "failed"`; link skipped with <2 decisions; map-reduce merge then link.
- [ ] 1.3 `tests/test_prompt_checks.py`: `known_scripts`.
- [ ] 1.4 `tests/test_llm_markdown.py` + `tests/golden/brief.md`; group order has brief first.
- [ ] 1.5 `tests/test_llm_api.py`, `test_settings.py`, `test_llm_prompts.py`, `test_prompts_api.py`: kind accepted,
      `max_tokens_brief`, prompt file synced.
- [ ] 1.6 `tests/js/outputs.test.mjs`, `compare.test.mjs`, page tests: Brief chip/tab first, panel sections, empty
      states, `changed from`, settled/open, `links: "failed"` note, compare counts.

## 2. Implement (green)
- [ ] 2.1 Schema + `schemas.py` (KINDS, code-field stripping). 1.1 green.
- [ ] 2.2 `prompts/brief/v1.md`, `checks.known_scripts`, settings/config. 1.3, 1.5 green.
- [ ] 2.3 Pipeline: merge, ids, `brief_link`. 1.2 green.
- [ ] 2.4 Markdown + golden; comparison counts. 1.4 green.
- [ ] 2.5 Page, prompts page, compare view. 1.6 green.
- [ ] 2.6 `make test` once.

## 3. Prove on real data
- [ ] 3.1 The two real runs in Verification, both models; results written into the proposal.
- [ ] 3.2 Screenshot of the Brief tab for each recording.

## 4. Close
- [ ] 4.1 validate, tick, archive, commit.
