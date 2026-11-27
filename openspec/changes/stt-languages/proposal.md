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

## Implementation notes (task 2.1)
mlx-whisper functions used, as in `transcribe.py:150-175`: `ModelHolder.get_model(repo, mx.float16)` (the same cached model
the decode uses), `audio.log_mel_spectrogram(x, n_mels=model.dims.n_mels, padding=N_SAMPLES)`, `audio.pad_or_trim(mel,
N_FRAMES, axis=-2).astype(float16)`, `model.detect_language(mel)` -> `(tokens, {code: prob over all languages})`.
The probabilities are taken on the audio the engine is given: with `overlap_s` that is the window plus 3 s before it, cut to
30 s, so the language is judged on slightly earlier audio than the window's own start. `pick_language` lives in
`engines/mlx_whisper.py`. Page: names of the extra languages come from `Intl.DisplayNames` in the browser (no list in script).

## Measured (2026-11-27, task 3.1)
`manos-filipGerman` (919.8 s, 31 windows), turbo, run through `details.engines.worker` directly with `language=el`,
`also=[en,de]`, `switch_min_share=0.7`, `overlap_s=3` (spec + output: scratchpad `lang-proof/`). Old run = run #3, Auto.
Wall: **139.4 s** total for the process (model load included) vs old **131.7 s** (+6%; the old run had no overlap, so
overlap and the detection pass are not separable here).
Windows per language: old Auto `el 17, en 13, de 1`; new `el 27, en 4, de 0` (new `en` = windows 3, 21, 24, 28).

| idx | start s | old | new | shares el/en/de | first segment of the new window |
|---|---|---|---|---|---|
| 0 | 0 | el | el | 0.87/0.13/0.00 | Ωραία! |
| 1 | 30 | el | el | 0.54/0.46/0.00 | Δεν είναι πολύ δυνατό |
| 2 | 60 | el | el | 0.61/0.39/0.01 | Γετρύγκτ |
| 3 | 90 | el | en | 0.26/0.72/0.02 | I don't think it would be a good word |
| 4 | 120 | el | el | 0.93/0.07/0.00 | Γεσχάιμπεν είπα, άντεξη |
| 5 | 150 | el | el | 0.93/0.07/0.00 | List |
| 6 | 180 | el | el | 0.87/0.13/0.00 | Μόνο αυτό, επειδή είναι E-I |
| 7 | 210 | en | el | 0.48/0.51/0.01 | Δεν πιστεύω ότι θα το αφήσω, θα το αφήσω τώρα |
| 8 | 240 | el | el | 0.34/0.65/0.01 | Υποχαλό. |
| 9 | 270 | el | el | 0.73/0.26/0.01 | Ναι, να ξέρε, πού είναι τα όμαλα |
| 10 | 300 | en | el | 0.44/0.55/0.01 | Γεσπράχεν |
| 11 | 330 | en | el | 0.83/0.17/0.00 | Έτσι και είναι τώρα και μετά τα άλλα είναι γιουβ |
| 12 | 360 | el | el | 0.42/0.57/0.01 | Γενόμεν |
| 13 | 390 | en | el | 0.57/0.42/0.01 | Τι, πώς είναι, δάμπλε και τάφ. |
| 14 | 420 | el | el | 0.71/0.28/0.00 | Δάπλε και τάφ |
| 15 | 450 | en | el | 0.49/0.50/0.01 | Εγώ θα πω... |
| 16 | 480 | el | el | 0.75/0.25/0.00 | Εγώ νομίζω αφάζεις μπ |
| 17 | 510 | en | el | 0.39/0.60/0.01 | Ευχαριστώ. |
| 18 | 540 | el | el | 0.87/0.13/0.00 | Υκοίια 2 |
| 19 | 570 | el | el | 0.62/0.38/0.01 | Να επιχειρήσουμε |
| 20 | 600 | de | el | 0.00/0.37/0.63 | Γεμάχτ. |
| 21 | 630 | en | en | 0.02/0.71/0.27 | Worked. |
| 22 | 660 | en | el | 0.42/0.58/0.01 | Φουακ |
| 23 | 690 | el | el | 0.55/0.44/0.01 | Γεγέσεν |
| 24 | 720 | en | en | 0.27/0.71/0.02 | I want to say that I can write |
| 25 | 750 | el | el | 0.40/0.59/0.01 | Γελέζει, όχι γελίζει. |
| 26 | 780 | el | el | 0.48/0.51/0.01 | Νίμντ. |
| 27 | 810 | en | el | 0.36/0.64/0.01 | Γεφώντεν. |
| 28 | 840 | en | en | 0.26/0.73/0.02 | It will be gone |
| 29 | 870 | en | el | 0.43/0.55/0.02 | ...βεστέχτετ |
| 30 | 900 | en | el | 0.44/0.56/0.00 | Καλημένα λοιπόν, γρήγορο stand up |
**Reading (text only; I cannot listen to audio).**
- Switched away from `el` (4 windows): w3 "I don't think it would be a good word" and w21 "Worked." are English words; w24
  "I want to say that I can write" and w28 "It will be gone" read as English sentences. All four shares are 0.71-0.73, just over
  the threshold. Whether Greek speakers really said English there needs listening; in the old run w21 was "I like this."
  and w24 "I want to say geschrieben", so English is plausible, German words inside English windows stay misspelled.
- Stayed `el` where the old run chose `en` (3 of 10): w10 old "gesprochen" -> new "Γεσπράχεν"; w11 old "That's right now and
  then the other one is Juvec a" -> new "Έτσι και είναι τώρα και μετά τα άλλα είναι γιουβ"; w30 old "I'm sorry" -> new
  "Καλημένα λοιπόν, γρήγορο stand up". The English the old run wrote there (w11, w17 "And the Versticht is the same") is
  mostly mis-heard Greek/German; the new text is Greek letters, German words spelled phonetically (the known Greek-forcing
  trade-off). w17 new "Ευχαριστώ." looks like a Whisper filler on a quiet window (not judged).
- The one window the old run decoded as German (w20, text "Kochen kocht gekocht. Lernen lernt gelernt...") has `de` 0.63,
  below 0.7, so it stayed Greek ("Γεμάχτ."): **a clearly German window was lost.**
- Shares of `en` between windows are mostly 0.4-0.6, only four reach 0.7; `de` is ~0 everywhere except w20.
**Threshold.** No value separates the cases cleanly from one recording. 0.7 is conservative (4 switches, all English-looking).
Lowering to 0.6 would also switch w20 (the German one) but also w8 (0.65, text "Υποχαλό."), w17 (0.60), w27 (0.64), which look
Greek. I did not tune it. Suggestion: keep 0.7 and re-judge by ear on the four `en` windows and w20 before changing.

## Open after the real run (orchestrator, 2026-11-27): the switch rule needs listening evidence
Per-window shares on `manos-filipGerman` show Whisper over-reads English on these Greek voices (clearly Greek windows
at en 0.55–0.65). The fixed `switch_min_share = 0.7` rule therefore misfires both ways:
- window 20 (10:00, German conjugation drill): el 0.00 / en 0.37 / de 0.63 → stayed `el`, German written in Greek letters;
- window 21 (10:30, German "arbeitet/hört/gehört"): el 0.02 / en 0.71 / de 0.27 → switched to `en` ("Worked… Gehurt").
Candidate rule (not implemented): stay in the primary unless its share is ≤ 0.10, then use the top other language.
On this run it moves window 20 to `de`, keeps 3/24/28 in Greek, and still sends 21 to `en`.
Decision waits for the owner to listen to windows 3 (1:30), 20 (10:00), 21 (10:30), 24 (12:00), 28 (14:00).
Until then the archive (task 4.1) stays open; the mechanism, storage, labels and page are done and tested.
