# Tasks: recording-capture

- [ ] 1. Write failing tests first (no implementation yet). DB tests use the foundation `clean_db` fixture and the async pool.
  - [ ] 1.1 `tests/test_recording_wavstream.py`: header patch makes `wave` read N frames; odd trailing byte trimmed; recovery of a file with stale header (size 0) gives correct duration.
  - [ ] 1.2 `tests/test_recording_migration.py`: 0002 applies on `details_test`; `script`/`recording` columns present; `script.slug` unique; `recording.script_id` FK.
  - [ ] 1.3 `tests/test_recording_seed.py`: seed sync inserts 4 scripts, is idempotent, updates body when file changes.
  - [ ] 1.4 `tests/test_recording_ws.py` (aiohttp test client): start/chunks/stop -> `saved`, row + `master.flac` 16000 Hz mono, `rec.wav` removed; `keep_wav=true` keeps it; 48 kHz start rejected; <0.5 s take discarded; WS drop then `resume` inside grace; drop with no resume finalizes after grace (grace set to 0.2 s in test); ffmpeg missing -> start error; fake ffmpeg that fails -> row points at `rec.wav`; low free disk -> start refused; WS upgrade with mismatched Origin rejected; message over `max_msg_size` closes the socket.
  - [ ] 1.5 `tests/test_recording_recovery.py`: killed session folders (`status=recording`, stale header) recovered at startup -> row, title ends " (recovered)", `source.recovered=true`; 0-frame folder removed.
  - [ ] 1.6 `tests/test_import_v1.py`: generated 1 s sine as `YYYYMMDD-HHMMSS.wav` + `.txt`; dry-run writes nothing and prints plan; real run imports; second run skips; corrupt wav counts as failed, exit code 1; `sessions/` untouched.
  - [ ] 1.7 `tests/test_record_page.py`: `/record` served 200 and contains title input, timer, mic button, tablist; `GET /api/scripts` returns 4 ordered scripts.
  - [ ] 1.8 Run `pytest -q tests`; **observe the new tests fail** (show output).
- [ ] 2. `db/migrations/0002_recording.sql`; tests 1.2 green.
- [ ] 3. Seed files `db/seeds/scripts/` (copy v1 scripts verbatim; `**Name:**` lines kept so speaker colours work) + `services/scripts.py` sync + `GET /api/scripts`; 1.3 and scripts part of 1.7 green.
- [ ] 4. `services/wavstream.py` (append, 5 s patch+fsync, atomic `meta.json`, header repair) and `services/audio.py` (ffmpeg/ffprobe wrappers, path from settings); 1.1 green.
- [ ] 5. `services/recordings.py` (create session, finalize, verify, row insert, fallback) + `api/record.py` WS (start/resume/stop, grace timer, preflight ffmpeg + free disk); 1.4 green.
- [ ] 6. Startup recovery hook in `app.py`; 1.5 green.
- [ ] 7. `import_v1.py` (+ dry-run table); 1.6 green.
- [ ] 8. `web/record/` page: v1 CSS tokens/look, title, timer, tabs (arrow keys, work while recording), meter + clip counter + dead-mic (peak < -60 dBFS for 10 s), 16 kHz guard, reconnect buffer, Saved card; 1.7 green. Add `[record]` to `config.toml` and `settings.py` (+ validation hook; test: `sample_rate=0` fails load), `*.flac` to `.gitignore`.
- [ ] 9. Run full suite `pytest -q tests`: all green (GPU-marked excluded), `openspec validate recording-capture --strict` passes.
- [ ] 10. Manual check in Brave: record 10 s, screenshot Record page (recording + Saved card); `ffprobe` the `master.flac`; `kill -9` the server mid-take, restart, confirm recovery; run importer `--dry-run` then real on `sessions/`, screenshot output; confirm `/live` still works.
