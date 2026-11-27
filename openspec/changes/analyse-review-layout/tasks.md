# Tasks: analyse-review-layout

## 1. Tests first (red; paste failures)
- [ ] 1.1 `tests/js/analyse.test.mjs`: `paragraphs` cases.
- [ ] 1.2 `tests/test_analyse_page.py` (+ JS page test): card order by id; toggle exists, default Paragraphs; Lines
      view renders `.seg` rows as before.
- [ ] 1.3 `tests/js/speakers.test.mjs` / `test_diar_page.py`: collapsed setup text when unavailable.

## 2. Implement (green)
- [ ] 2.1 `paragraphs` helper. 1.1 green.
- [ ] 2.2 Card order + toggle + paragraph rendering with per-span seek and playing highlight. 1.2 green.
- [ ] 2.3 Who talked collapsed setup. 1.3 green.
- [ ] 2.4 `make test` once.

## 3. Prove on real data
- [ ] 3.1 Screenshots as in Verification (needs the app restarted on this code).

## 4. Close
- [ ] 4.1 validate, tick, archive, commit.
