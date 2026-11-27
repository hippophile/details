# Tasks: analyse-review-layout

## 1. Tests first (red; paste failures)
- [x] 1.1 `tests/js/analyse.test.mjs`: `paragraphs` cases.
- [x] 1.2 `tests/test_analyse_page.py` (+ JS page test): card order by id; toggle exists, default Paragraphs; Lines
      view renders `.seg` rows as before.
- [x] 1.3 `tests/js/speakers.test.mjs` / `test_diar_page.py`: collapsed setup text when unavailable.

## 2. Implement (green)
- [x] 2.1 `paragraphs` helper. 1.1 green.
- [x] 2.2 Card order + toggle + paragraph rendering with per-span seek and playing highlight. 1.2 green.
- [x] 2.3 Who talked collapsed setup. 1.3 green.
- [x] 2.4 `make test` once.

## 3. Prove on real data
- [x] 3.1 Screenshots as in Verification (needs the app restarted on this code). — done 2026-11-27: write-up first, paragraph view; found 4 follow-ups, fixed in 3b

## 3b. Follow-ups from the browser check
- [x] 3b.1 Red tests: char loop collapsed (and 7 repeats kept); CSS rule present; chapter list unbulleted; static
      response carries `Cache-Control: no-cache`.
- [x] 3b.2 Implement; `make test` once.

## 4. Close
- [x] 4.1 validate, tick, archive, commit.
