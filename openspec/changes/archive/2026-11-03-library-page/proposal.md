# library-page

## Driver
From `design/v2/DESIGN.md` / `designer.md` D3: "Library is the only place to pick a recording. Analyse never shows a file picker, it gets `?rec=id`." Plan row 3: "List / search / play / rename / delete (trash) recordings, import an audio file (ffmpeg)." Data rule: "Deleting: a 30-day trash, then purge."

## Depends on
`v2-foundation`, `recording-capture` (needs table `recording` with `deleted_at`, `size_bytes`, `source`; `services/audio.py` ffmpeg/ffprobe wrappers; `services/recordings.py`; `web/shared/`). Blocks `stt-runs` (Analyse is reached from here).

## Measured blast radius (2026-11-03)
- Repo today has no `src/`, `web/`, `db/`; `tests/` has 1 file. **No migration, no new table.**
- Created: `src/details/api/recordings.py`, `src/details/services/{library.py,trash.py}`, `web/library/{index.html,library.js}`, `tests/test_library_*.py`.
- Modified: `src/details/app.py` (routes + purge task), `config.toml` (`[trash] days`, `[import] max_mb`), `src/details/settings.py` (trash + import sections, validation hooks: `trash.days>=1`, `import.max_mb>0`; env `DETAILS_TRASH_DAYS`, `DETAILS_IMPORT_MAX_MB`), `src/details/services/audio.py` (add `probe()` / `normalise()` if recording-capture did not).
- Not touched: `live/*`, `eval/*`, Record page, DB schema.

## Decision
1. **API** (`api/recordings.py`): `GET /api/recordings?q=&trash=0|1`, `GET /api/recordings/{id}`, `PATCH /api/recordings/{id}` (title), `DELETE /api/recordings/{id}` (to trash), `POST /api/recordings/{id}/restore`, `GET /api/recordings/{id}/audio` (range-capable), `POST /api/recordings/import` (multipart).
2. **List item**: `id, title, started_at, duration_ms, size_bytes, runs_count (always 0 until stt-runs fills it), deleted_at`. Search = case-insensitive substring on title, `%`/`_` escaped.
3. **Audio endpoint**: serves `audio_path` under the data dir only, `Accept-Ranges: bytes`, 206/416 handled, Content-Type from container. Path is resolved and must stay inside `${DETAILS_DATA_DIR}/recordings/`.
4. **DB/security**: async pool only; POST/PATCH/DELETE pass the foundation Origin check. **Trash**: delete sets `deleted_at=now()`; Library has a "Trash" toggle with Restore; confirm dialog before delete. `services/trash.py` purge (startup + every 24 h): rows older than `trash.days` (default 30, must be >= 1) are skipped if the recording has a queued or running job; otherwise the row is `DELETE`d (all FKs, incl. `job`, cascade) and `<data_dir>/recordings/<id>` plus `<data_dir>/jobs/<job_id>` for each of its jobs are removed.
5. **Import**: stream upload to `recordings/<uuid>/original.<ext>` (never modified), cap `settings.import.max_mb` (default 2048), preflight free disk >= 2x the upload size (Content-Length, else the cap) -> 507 before writing, ffprobe must find an audio stream, ffmpeg `-vn -map_metadata -1 -ar 16000 -ac 1` -> `master.flac`, same verification and row shape as recording-capture (`source {"import":"file","original":...}`), title = file name stem.
6. **Page** `web/library/`: table on desktop, cards under 700 px, native `<audio controls>` per row (loaded on demand), rename inline, delete with confirm, Trash toggle, import button (file input + drop target) with progress text, "Analyse" link to `/analyse?rec=<id>`, runs column showing `0`.

## Verification
- Tests first against `details_test` with a fake data dir and real ffmpeg on tiny synthetic audio.
- Negative cases: import with free disk < 2x size -> 507, no folder;  Range `bytes=999999999-` -> 416; path traversal in `audio_path` -> refused; empty/`<img>` title; file with no audio stream -> 422 and no leftover folder; oversize upload -> 413 and cleanup; delete of unknown id -> 404; purge leaves rows younger than the limit.
- Manual: screenshot of Library (desktop + 390 px), play and seek in Brave, import an `.m4a` and an `.mp4`, delete -> Trash -> Restore.

## Caveats
- **`/analyse` does not exist until `stt-runs`**: the Analyse link 404s until then. Runs count is a hard-coded `0` placeholder, replaced in `stt-runs`.
- **Purge must cascade**: per the integration contract every FK to `recording` (incl. `job`) is `ON DELETE CASCADE`; the purge test here inserts minimal `job` rows only if that table exists, otherwise that part is marked 'extended when those tables exist' (honest gap: stt_run/analysis_run cascade is verified by their own changes).
- Import is always mono (v2.0 is mic-only); a stereo file is downmixed. Stereo-preserving import comes with v2.2.
- Import runs inside the request (no job runner yet): a 2.5 h file may take a minute or more (estimate, not measured). Closing the tab mid-upload aborts it and cleans up; once conversion has started it finishes server-side.
- No "Delete now" (skip trash) and no running-jobs banner: both v2.1 / `jobs-and-models`.
- FLAC playback relies on Brave (Chromium); Safari/QuickTime unverified.
