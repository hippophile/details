## ADDED Requirements

### Requirement: List and search recordings
`GET /api/recordings` SHALL return non-trashed recordings newest first with `id, title, started_at, duration_ms, size_bytes, runs_count, deleted_at`, SHALL filter by case-insensitive substring of the title when `q` is given (treating `%` and `_` literally), and SHALL return `runs_count` 0 until stt-runs exists. With `trash=1` it SHALL return only trashed recordings.

#### Scenario: Newest first
- **GIVEN** recordings started at 10:00 and 11:00
- **WHEN** the list is requested
- **THEN** the 11:00 recording comes first

#### Scenario: Search
- **GIVEN** titles "Standup 3 Nov" and "BPMN review"
- **WHEN** `q=standup` is requested
- **THEN** only "Standup 3 Nov" is returned

#### Scenario: Wildcard literal
- **GIVEN** titles "100% done" and "plain"
- **WHEN** `q=%` is requested
- **THEN** only "100% done" is returned

#### Scenario: Trash hidden
- **GIVEN** one trashed recording
- **WHEN** the list is requested without `trash`
- **THEN** it is absent, and present with `trash=1`

### Requirement: Range-capable audio playback
`GET /api/recordings/{id}/audio` SHALL stream the recording's file with `Accept-Ranges: bytes`, answer valid Range requests with 206 and `Content-Range`, answer unsatisfiable ranges with 416, return 404 for unknown or trashed ids, and SHALL refuse any `audio_path` that resolves outside `${DETAILS_DATA_DIR}/recordings/`.

#### Scenario: Full file
- **GIVEN** a recording with a 1 MB master
- **WHEN** the audio is requested without Range
- **THEN** status is 200, `Accept-Ranges: bytes`, and the body length is 1 MB

#### Scenario: Seek
- **GIVEN** the same recording
- **WHEN** `Range: bytes=100-199` is sent
- **THEN** status is 206, `Content-Range: bytes 100-199/<size>`, body is 100 bytes equal to that slice

#### Scenario: Beyond the end
- **GIVEN** the same recording
- **WHEN** `Range: bytes=99999999-` is sent
- **THEN** status is 416

#### Scenario: Traversal
- **GIVEN** a row whose `audio_path` is `../../etc/hosts`
- **WHEN** its audio is requested
- **THEN** the server refuses and serves no file

#### Scenario: Native player
- **GIVEN** the Library page lists a recording
- **WHEN** the user presses play on its `<audio controls>`
- **THEN** the element's source is `/api/recordings/<id>/audio`

### Requirement: Rename
`PATCH /api/recordings/{id}` with `{title}` SHALL trim the title, require 1 to 200 characters, store it, and the page SHALL render titles as text only.

#### Scenario: Rename
- **GIVEN** a recording "Recording 2026-11-03 10:02"
- **WHEN** the user renames it to "  Standup 3 Nov "
- **THEN** the stored title is "Standup 3 Nov" and the list shows it

#### Scenario: Invalid title
- **GIVEN** any recording
- **WHEN** a title of only spaces, or 201 characters, is sent
- **THEN** the response is 422 and the title is unchanged

#### Scenario: Markup in title
- **GIVEN** title `<img src=x onerror=alert(1)>`
- **WHEN** the list renders
- **THEN** it is shown as literal text

### Requirement: Delete to trash with confirmation
Deleting a recording SHALL require a confirmation dialog in the page, and `DELETE /api/recordings/{id}` SHALL set `deleted_at` without removing files or the row; `POST /api/recordings/{id}/restore` SHALL clear `deleted_at`; the page SHALL offer a Trash view with Restore.

#### Scenario: Confirm required
- **GIVEN** the Library shows a recording
- **WHEN** the user presses Delete and then Cancel in the dialog
- **THEN** no request is sent and the recording stays

#### Scenario: Move to trash
- **GIVEN** the user confirms the dialog
- **WHEN** the request completes
- **THEN** `deleted_at` is set, the row leaves the main list, and the audio file still exists

#### Scenario: Restore
- **GIVEN** a trashed recording
- **WHEN** Restore is pressed in the Trash view
- **THEN** `deleted_at` is null and it appears in the main list

#### Scenario: Unknown id
- **GIVEN** an id that does not exist
- **WHEN** DELETE is requested
- **THEN** the response is 404

### Requirement: Trash purge
The system SHALL permanently delete (row, `<data_dir>/recordings/<id>`, and `<data_dir>/jobs/<job_id>` for each of its jobs; all FKs cascade) every recording whose `deleted_at` is older than `settings.trash.days` (default 30, env `DETAILS_TRASH_DAYS`, minimum 1 enforced by the section validation hook), at startup and every 24 hours, SHALL skip recordings with a queued or running job, and SHALL never purge a non-trashed recording.

#### Scenario: Old trash purged
- **GIVEN** a recording trashed 31 days ago and `trash.days` 30
- **WHEN** the purge runs
- **THEN** the row and its folder are gone

#### Scenario: Active job blocks purge
- **GIVEN** a recording trashed 40 days ago with a running job
- **WHEN** the purge runs
- **THEN** it is skipped and purged on a later run after the job ends

#### Scenario: Job files removed
- **GIVEN** a recording trashed 40 days ago with a finished job `J`
- **WHEN** the purge runs
- **THEN** the row, `recordings/<id>` and `jobs/J` are gone

#### Scenario: Young trash kept
- **GIVEN** a recording trashed 29 days ago
- **WHEN** the purge runs
- **THEN** it remains restorable

#### Scenario: Configurable
- **GIVEN** `DETAILS_TRASH_DAYS=7` and a recording trashed 8 days ago
- **WHEN** the purge runs
- **THEN** it is purged

#### Scenario: Invalid setting
- **GIVEN** `trash.days` set to 0
- **WHEN** settings load
- **THEN** startup fails with a message naming `trash.days`

### Requirement: Import audio or video file
`POST /api/recordings/import` SHALL stream the upload to `recordings/<uuid>/original.<ext>` without modifying it, enforce `settings.import.max_mb`, check free disk >= 2x the upload size, require ffprobe to find an audio stream, convert with ffmpeg (`-vn -map_metadata -1 -ar 16000 -ac 1`) to `master.flac`, verify duration, insert a `recording` row (title = file name stem, source `{"import":"file","original":...}`) and leave no files behind on any failure.

#### Scenario: Audio file
- **GIVEN** a 5 s `.m4a`
- **WHEN** it is uploaded
- **THEN** the row has 16000 Hz, 1 channel, duration 5000 ms +/- 50
- **AND** `original.m4a` is byte-identical to the upload

#### Scenario: Video file
- **GIVEN** an `.mp4` with video and audio
- **WHEN** it is uploaded
- **THEN** `master.flac` has audio only and the original keeps its video

#### Scenario: Not audio
- **GIVEN** a text file named `notes.wav`
- **WHEN** it is uploaded
- **THEN** the response is 422 and no folder or row exists

#### Scenario: Too big
- **GIVEN** `import.max_mb` is 1 and a 2 MB upload
- **WHEN** it is uploaded
- **THEN** the response is 413 and no folder remains

#### Scenario: Not enough disk
- **GIVEN** free disk below 2x the upload size
- **WHEN** an upload arrives
- **THEN** the response is 507 and nothing is written

#### Scenario: Foreign origin
- **GIVEN** a POST import with a mismatched Origin
- **WHEN** it reaches the server
- **THEN** it is rejected by the foundation middleware

#### Scenario: ffmpeg missing
- **GIVEN** ffmpeg cannot be found
- **WHEN** an upload arrives
- **THEN** the response is 503 with a message naming ffmpeg

#### Scenario: Unsafe file name
- **GIVEN** an upload named `../../x.m4a`
- **WHEN** it is stored
- **THEN** the stored name is `original.m4a` inside the new folder

### Requirement: Library page
The page `web/library/` SHALL list recordings as a table on wide screens and cards below 700 px, with title, date, length, size and runs count, a search box, rename, delete, Trash toggle, an import control with progress text, and for each recording an Analyse link to `/analyse?rec=<id>`; it SHALL use only native controls and keep the v1 look.

#### Scenario: Columns
- **GIVEN** a recording "Standup 3 Nov", 92 s, 2.9 MB
- **WHEN** the Library loads
- **THEN** the row shows the title, local date and time, "01:32", "2.9 MB" and runs "0"

#### Scenario: Analyse link
- **GIVEN** a recording with id X
- **WHEN** the row renders
- **THEN** its Analyse link href is `/analyse?rec=X`

#### Scenario: Empty state
- **GIVEN** no recordings
- **WHEN** the Library loads
- **THEN** a message with a link to Record is shown instead of an empty table

#### Scenario: Import feedback
- **GIVEN** the user chooses a file
- **WHEN** the upload and conversion run
- **THEN** a status line says "Importing..." and, on success, the new recording appears at the top
- **AND** on failure the server's error message is shown

#### Scenario: Phone width
- **GIVEN** a viewport of 390 px
- **WHEN** the page renders
- **THEN** rows appear as cards and there is no horizontal scroll
