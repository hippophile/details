# Tasks: monday-brief

## 1. Tests first (red; paste failures)
- [x] 1.0 Re-measure with grep: every place the four kinds are listed (`KINDS`, `max_tokens_*`, `outputs.js`,
      `prompts.js`, `compare.js`, `comparison.output_counts`, `markdown.render_output`, tests that assert the kind
      list or a 422 message) and any DB CHECK on `kind`. Write the counts into the proposal.
- [x] 1.1 `tests/test_llm_schemas.py`: `brief.v1` good/bad fixtures; model schema has none of the code-written fields;
      the four existing kinds' model schemas are byte-identical to before.
- [x] 1.2 `tests/test_llm_pipeline.py` (fake chat): ids and order; link applied; each invalid-link case retried once;
      failed link keeps the brief with `links: "failed"`; link skipped with <2 decisions; map-reduce merge then link.
- [x] 1.3 `tests/test_prompt_checks.py`: `known_scripts`.
- [x] 1.4 `tests/test_llm_markdown.py` + `tests/golden/brief.md`; group order has brief first.
- [x] 1.5 `tests/test_llm_api.py`, `test_settings.py`, `test_llm_prompts.py`, `test_prompts_api.py`: kind accepted,
      `max_tokens_brief`, prompt file synced.
- [x] 1.6 `tests/js/outputs.test.mjs`, `compare.test.mjs`, page tests: Brief chip/tab first, panel sections, empty
      states, `changed from`, settled/open, `links: "failed"` note, compare counts.

## 2. Implement (green)
- [x] 2.1 Schema + `schemas.py` (KINDS, code-field stripping). 1.1 green.
- [x] 2.2 `prompts/brief/v1.md`, `checks.known_scripts`, settings/config. 1.3, 1.5 green.
- [x] 2.3 Pipeline: merge, ids, `brief_link`. 1.2 green.
- [x] 2.4 Markdown + golden; comparison counts. 1.4 green.
- [x] 2.5 Page, prompts page, compare view. 1.6 green.
- [x] 2.6 `make test` once.

## 3. Prove on real data
- [x] 3.1 The two real runs in Verification, both models; results written into the proposal.
- [x] 3.2 Screenshot of the Brief tab for each recording. — manos-filipGerman only (analysis #6, Gemma 12B: lesson, nothing decided, 1 item left out); giannis-vg-html not re-run in the app

## 3b. Code enforcement after the real runs
- [x] 3b.1 Red tests for the three rules (lesson empties lists + counts; unknown name nulled, spoken name kept,
      labelled-run name kept; disagreement with 1 found position dropped).
- [x] 3b.2 Implement in the pipeline (single pass and after merge). Green; `make test` once.
- [x] 3b.3 Re-process the three saved briefs (no model call); report before/after.

## 4. Close
- [x] 4.1 validate, tick, archive, commit.
