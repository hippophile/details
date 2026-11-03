# recording-capture

## Driver
From `design/v2/DESIGN.md`: "the server writes a crash-safe WAV *while* you talk. On Stop it turns it into lossless FLAC. Test-script tabs stay." Also the v1 bug: "the v1 recorder downsamples 48k to 16k without a filter, which causes aliasing. v2 uses `AudioContext({sampleRate:16000})` instead." And: "v1 stays working at `/live`."

## Depends on
`v2-foundation` (needs: `src/details` package, settings loader, migration runner with 0001 applied, `db.py`, aiohttp app serving `web/` on 127.0.0.1, `/live` mount, `DETAILS_DATA_DIR`). Nothing else. Blocks `library-page`.

## Measured blast radius (2026-11-03)
- Repo today: no `src/`, `web/`, or `db/` yet (foundation creates them). `tests/` has 1 file (`test_score.py`, 26 lines).
- Created: `db/migrations/0002_recording.sql`; `db/seeds/scripts/*.md` (4 files); `src/details/api/record.py`; `src/details/services/{recordings.py,wavstream.py,audio.py,scripts.py}`; `src/details/import_v1.py`; `web/record/{index.html,record.js,worklet.js}`; `web/shared/{shared.css,nav.js}` only if foundation has not made them; `tests/test_recording_*.py`, `tests/test_import_v1.py`.
- Modified: `src/details/app.py` (register routes, startup recovery + seed sync), `config.toml` (new `[record]` section), `src/details/settings.py` (record section + validation hook), `.gitignore` (+`*.flac`).
- Read-only (untouched): `live/index.html` (222 lines, 3 scripts at lines 85-117), `live/server.py` (153 lines), `eval/script_meeting_el_en.md` (60 lines, 22 spoken lines; `eval/score.py` still reads it, so the seed is a copy, not a move), `sessions/` (1 sample: 12 s wav 384 KB + txt).

## Decision
1. **Migration 0002** creates `script(id, slug unique, name, body_md, sha256, sort_order, created_at)` and `recording(id, title, started_at, duration_ms, audio_path, audio_sha256, container, codec, sample_rate, channels, channel_map jsonb, source jsonb, script_id fk null, notes, size_bytes, deleted_at null, created_at)`. `audio_path` is relative to the data dir. Deviation from `architect-data.md`: `slug`, `sort_order`, `size_bytes` added (stable seed key, tab order, library size column without stat-ing files).
2. **Seed scripts** in `db/seeds/scripts/NN-slug.md` (front matter `slug`, `name`): the 3 v1 tab scripts (text copied verbatim from `live/index.html`) + the long eval script. Synced at startup, insert-or-update by slug; sha256 stored.
3. **DB and security**: all DB access via the foundation `AsyncConnectionPool` (no sync calls on the event loop); the WS upgrade passes through the foundation Origin-check middleware (mismatched Origin rejected); WS sets explicit `max_msg_size`. Pages are `/record` and `/library` (foundation serves `web/<x>/index.html`); links are `/analyse?rec=<id>` and `/library`.
3b. **Capture** in `web/record/`: `AudioContext({sampleRate:16000})`, EC/NS/AGC off, mono; worklet only meters + emits 100 ms int16 blocks (no resampling code). If the browser gives another rate the page refuses to start (never silently resamples).
4. **WS `/api/record/ws`**: `start{title,script_id,sample_rate}` -> `ready{recording_id}`; binary chunks; `stop` -> `saved{recording}`. Server appends to `${DETAILS_DATA_DIR}/recordings/<uuid>/rec.wav`, patches header + fsyncs every `settings.record.sync_interval_s` (5), writes `meta.json` atomically (`recording|finalizing|done|recovered`).
5. **Finalize** (`services/recordings.py`): ffmpeg `rec.wav` -> `master.flac`, ffprobe duration must match WAV data length within 50 ms, sha256, insert `recording` row, delete `rec.wav` unless `record.keep_wav`. On any failure keep `rec.wav` and insert the row pointing at it (audio never lost).
6. **Drop + crash**: WS drop -> `interrupted`, `resume{recording_id}` accepted for `record.grace_s` (60) then auto-finalize; browser buffers unsent chunks and reconnects. Startup scans `meta.json` with status `recording|finalizing`, fixes header (truncate to whole frames), finalizes as `recovered`.
7. **Record page UI**: v1 look, title input, mm:ss timer, script tabs from `GET /api/scripts`, level meter + clip counter + dead-mic warning, "Saved" card (Analyse now -> `/analyse?rec=<id>`, Open library -> `/library`, New recording).
8. **Importer** `python -m details.import_v1 [--dir sessions] [--dry-run]`: idempotent (by wav sha256 in `source`), copies not moves, writes `master.flac`; `.txt` kept as `v1-transcript.txt` beside it.
9. **Settings**: nested section `[record]` in config.toml -> `settings.record.<key>`, env `DETAILS_RECORD_<KEY>`; validation (sample_rate>0, sync_interval_s>0, grace_s>=0, min_free_gb>=0) lives in the section's validation hook in `settings.py`. Keys: `sample_rate=16000`, `record.sync_interval_s=5`, `record.grace_s=60`, `record.keep_wav=false`, `record.min_free_gb=2`, `ffmpeg`/`ffprobe` paths (empty = `shutil.which`; a non-empty path must exist, no fallback), `max_msg_mb` (WS `max_msg_size`, explicit, default 1), `tz` (default Europe/Athens, alias env `DETAILS_TZ`, used by the importer).
10. **Live preview text: DEFERRED.** Keeping v1 `/live` for live text; Record page only links to it. Reason: needs a model-holding GPU thread competing with the future job runner (change 4).

## Verification
- Tests first (red run shown), default suite uses real ffmpeg on 2 s synthetic sine, fake WS client, `details_test` DB. No GPU.
- Negative cases: WS client killed mid-stream -> file recoverable with correct duration; truncated odd-byte tail trimmed; 48 kHz `start` rejected; ffmpeg missing -> start refused with clear error; corrupt FLAC (fake ffmpeg) -> row points at `rec.wav`; importer re-run -> 0 imported / 1 skipped.
- Manual: record 10 s in Brave, screenshot of Record page + Saved card, `ffprobe master.flac` shows 16000 Hz mono; kill -9 server mid-take, restart, recording appears.

## Caveats
- **Aliasing fix is not measured.** It relies on Chromium honouring `sampleRate:16000`; the guard refuses to record otherwise.
- **Whole-Mac crash** can lose up to ~5 s of unsynced data (estimate, not measured).
- **Sleep/lid-close** drops audio silently; this change logs `visibilitychange` gaps in `meta.json` and shows a "keep the Mac awake" hint, but does NOT run `caffeinate` (v2.1).
- FLAC playback in QuickTime is unverified; the Library plays it in the browser.
- Importer does not create `stt_run` rows (table arrives in 0004). Differs from `architect-data.md` 2.10 steps 3-4 by design: ``stt-runs` now adopts `v1-transcript.txt` as an `stt_run` (per SPEC-RULES integration contract); `score.py` outputs import stays with `stt-runs`/later.
- Browser-side reconnect buffer is capped at 10 min (19 MB); beyond that the oldest chunks are dropped and a warning is shown.
- Mic only, mono. Stereo / tab audio are v2.2.
