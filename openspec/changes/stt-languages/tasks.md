# Tasks: stt-languages

## 1. Tests first (red; paste failures)
- [ ] 1.1 `tests/test_stt_api.py`: `also` accepted and stored; 422 for unknown code, duplicate, primary repeated,
      `also` with `auto`, non-list.
- [ ] 1.2 `tests/test_settings.py`: `stt.extra_languages`, `stt.switch_min_share` load, env override, bad values rejected.
- [ ] 1.3 `tests/test_stt_worker.py`: window records carry `language` and `lang_probs` (fake engine, `FAKE_STT_LANGS`).
- [ ] 1.4 `tests/test_stt_step.py` / `test_stt_storage.py`: Auto run stores the majority language and `raw.languages`;
      labels contain `Auto`, `Ελληνικά`, `Ελληνικά + en + de`; old rows without `also` still label.
- [ ] 1.5 New `tests/test_mlx_lang_pick.py`: pure function `pick_language(probs, primary, also, min_share)`.
- [ ] 1.6 `tests/js/analyse.test.mjs`: `pickRun` prefers newest accurate; page test: checkboxes exist, disabled on Auto,
      POST body carries `also`. `/api/models` returns `stt_languages`.

## 2. Implement (green)
- [ ] 2.1 Read the installed `mlx_whisper` source; write the functions used for language probabilities into the proposal.
- [ ] 2.2 `pick_language` (pure) + adapter + worker record. 1.3, 1.5 green.
- [ ] 2.3 Settings/config, API validation, run settings. 1.1, 1.2 green.
- [ ] 2.4 Step storage + `run_label` + `model_role`. 1.4 green.
- [ ] 2.5 `/api/models` key, page checkboxes, `pickRun`. 1.6 green.
- [ ] 2.6 `make test` once.

## 3. Prove on real data
- [ ] 3.1 Real run as in Verification; table of windows (language, shares) in the proposal; listening notes.

## 4. Close
- [ ] 4.1 validate, tick, archive, commit.
