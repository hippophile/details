# stt-languages: say which languages are spoken, and label runs honestly

## Driver
Owner, 2026-11-27 (backlog M2, S6). On `manos-filipGerman` (Greek speakers drilling German verbs):
- Run #3 (turbo, **Auto**) decoded 13 of 31 windows as English and wrote English nobody said ("How did you test
  this?" at 15:12); run #1 (large, Auto) decoded one window as Icelandic.
- Forcing Greek writes German in Greek letters ("Γετρούγκεν").
- The run list shows run #3 as `el`: an Auto run is stored with the FIRST window's language
  (`runner/steps_stt.py:74`, `COALESCE(language, lang_seen)`), and the label has no language at all, so two runs of
  the same model with different settings look identical.

## Depends on
`stt-clean-windows` (same files; implement after it).

## Today (read 2026-11-27)
- `services/stt_runs.py:10` `LANGUAGES = ("el", "auto")`; `api/stt_runs.py:26` passes `language`.
- `engines/mlx_whisper.py:25` passes `language=None` for auto, so mlx-whisper picks from ~100 languages per window.
- `web/analyse/index.html:37` offers Ελληνικά / Auto. `web/analyse/api.js:31` `pickRun` takes the first done run.

## Decision
1. **Request.** `POST /api/recordings/{id}/stt-runs` accepts optional `also: ["en","de"]`: other languages spoken in
   the recording. Each code must be in `[stt] extra_languages` (config, default `"en,de"`, env
   `DETAILS_STT_EXTRA_LANGUAGES`), no duplicates, not the primary; only with `language != "auto"`; else 422.
   Stored in `stt_run.settings.also` (list, `[]` when absent).
2. **Per-window choice inside the allowed set.** With `also` non-empty the engine, for each window, asks Whisper for
   language probabilities, keeps only primary + `also`, renormalises, and decodes the window in a non-primary
   language only when its share is at least `[stt] switch_min_share` (default `0.7`, range 0.5–1.0); otherwise in
   the primary. Whisper decodes one language per window; this picks which one, it does not mix inside a window.
3. **Engine contract.** `settings` gains `also` and `switch_min_share`. The engine sets `self.language` (used) and
   `self.lang_probs` (`{code: share}` over the allowed set, 2 decimals; `None` when not computed). The worker writes
   both into the window record. Engines without language probabilities ignore `also` (fake engine: scripted via
   env `FAKE_STT_LANGS`).
4. **mlx-whisper adapter** computes the probabilities itself (model + mel of the window's first 30 s +
   `detect_language`). The exact functions of the installed mlx-whisper are an assumption: task 2.1 reads the
   installed source first and records what it used in this proposal.
5. **Honest storage and label.** On completion `stt_run.language` = the primary for `el` runs; for Auto runs the
   language of the most windows (ties: earliest). `raw.languages` = `{code: windows}`. `run_label` gains the
   requested setting after the model label: `Ελληνικά`, `Ελληνικά + en + de`, or `Auto`
   (`#3 · Whisper large-v3 turbo · Auto · 2:12 · 7.0×`). Existing rows are relabelled from `settings.language`
   (no migration: the label is computed at read time).
6. **Page.** Next to the Language select: one checkbox per extra language ("Also spoken: ☐ English ☐ Deutsch"),
   disabled when Auto is chosen. The list comes from `GET /api/models` (new key `stt_languages:
   {primary: ["el"], extra: ["en","de"]}`); no language code is hard-coded in page script.
7. **Which run opens first (S6).** Runs carry `model_role` from the registry. `pickRun`: among done, non-live-preview
   runs prefer the newest whose `model_role` is `accurate`; else today's rule.

## Not in this change
Mixing two languages inside one 30 s window; German-specific models; translating.

## Verification
- Red first: API validation (422 cases), settings, worker window records with `lang_probs`, step storage
  (`language`, `raw.languages`), label text, `pickRun` (node test), page checkboxes.
- Adapter: unit test with a stub model object proving restriction + threshold (0.69 → primary, 0.71 → other).
- Real (gpu, by hand): `manos-filipGerman`, turbo, `el` + `also: en,de`, run through the worker child directly.
  Report per-window language and shares next to run #3's (13 `en` windows). Listen to 3 windows that switched and
  3 that did not; write in this proposal whether the choice matched the audio. Tune `switch_min_share` only from
  that evidence.

## Caveats
- The threshold 0.7 is a guess until the real run; the proposal records the measured shares.
- A window with Greek chat *and* German words is still decoded in one language: German inside Greek windows stays
  in Greek letters. This reduces whole-window errors, not word-level ones.
- One extra language-detection pass per window (encoder only): small extra time, measured in the real run.

## Corrections after the independent audit (2026-11-27)
- **Run settings are frozen at start.** `also` and `switch_min_share` are stored in `stt_run.settings` by `start_run`;
  the step reads them from there (same reason as `stt-clean-windows`: `spec.json` is rewritten on resume).
- **The runs list stays small.** `LIST_COLS` has no `raw` (it holds every window's segments). The step also writes a
  small `stt_run.settings.languages = {code: windows}` copy? No: settings are the request. Instead the list query adds
  `raw->'languages' AS languages` (one JSON path, not the whole `raw`). `run_label` gains an optional `setting`
  argument built from `settings.language` + `settings.also`; v1-import rows (no `settings.language`) show no language.
- **Where the page gets the language list.** `/api/models?kind=stt` returns a flat list and a page test asserts that
  URL, so it is not changed. New `GET /api/models?kind=stt_languages` → `{primary: ["el"], extra: ["en","de"]}`;
  the 422 message lists the new kind.
- **pickRun caveat, made a rule.** Preferring `accurate` alone would open an old, uncleaned large run over a newer
  cleaned turbo run. Order: newest done non-preview run that is `accurate` **and** has `raw.cleaned`; else newest
  that has `raw.cleaned`; else today's rule. The list query exposes `cleaned` as a boolean.
- Confirmed feasible (audit read installed mlx-whisper): `model.detect_language(mel)` with `log_mel_spectrogram` +
  `pad_or_trim(N_FRAMES)` and the dtype cast, as in `transcribe.py:150-175`.
