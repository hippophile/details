# record-pause: Start / Pause↔Continue / End buttons on the Record page

## Driver
User, 2026-11-05: "i want one more functionality the buttons 1 start recording 1 pause (if i click it becomes resume) and 1 end recording".

## Depends on
Archived: recording (WS `/api/record/ws`, `WavWriter`, `finalize`, `Session.gaps`), live-assistant (`LiveSession.offer`, segmenter).

## Measured blast radius (repo at 2026-11-05)
- `web/record/index.html`: one `#mic` button today (toggles Mic on / Stop).
- `web/record/record.js` (234 lines): `onBlock` queues every 100 ms block, the timer = `blocks / 10`, and the gap detector sends `{"type":"gap"}` when no block has arrived for >1.5 s.
- `src/details/api/record.py` (139 lines): handles `start`, `resume` (= **WS reconnect**, already taken), `gap` and `stop`. Anything else → `unexpected message`.
- `src/details/services/recordings.py` (200 lines): `finalize` builds `source` (capture, interrupted, recovered, gaps).
- `src/details/live/session.py` (601 lines): `offer(data, frames_before)` feeds the segmenter.
- Edited: `web/record/{index.html,record.js,live.js}`, `src/details/api/record.py`, `src/details/services/recordings.py`, `src/details/live/{session,segmenter,rolling}.py`. New: `web/record/pause.js`, `tests/js/pause.test.mjs`, pytest wrapper `tests/test_record_pause_js.py`. Tests edited: `tests/test_record_page.py`, `tests/test_recording_ws.py`, `tests/test_recording_recovery.py`, `tests/test_live_session.py`, `tests/test_live_segmenter.py`, `tests/js/live.test.mjs`, `tests/e2e/test_live_brave.py`.

## Decision (revised after review, 2026-11-05)
1. **Buttons:**
   - `#mic` stays and is labelled **Start**. `#pause` is labelled Pause, and Continue while paused. `#end` is labelled **End**.
   - Pause and End are enabled once a recording id exists and the page is not stopping: `recId != null && !stopping`. They do **not** depend on `ready`, so End and Pause work during a reconnect. Before the first `ready`, Pause is disabled and End behaves as Stop does today (the stop is queued).
   - While "Finishing…", all three are disabled. `setUi`, `finish`, `fail` and `start` reset the paused flag and the labels, so nothing leaks into the next take.
   - Texts that say "Mic on" now say "Start".
2. **Ordered control messages:**
   - Pause and Continue are queued as ordered sentinel items in the same `queue` as the audio blocks. `flush()` sends them in order, so blocks recorded before the pause always arrive before it, even across a disconnect.
   - The wire names are `{"type":"pause"}` and `{"type":"continue"}`. `resume` stays the reconnect message.
3. **Server is idempotent and never fatal:**
   - `api/record.py` gets an explicit `elif kind in ("pause","continue")` branch, placed before the final else.
   - These are **silently ignored**, with only a log line: a pause or continue with no session, a duplicate pause, and a continue while not paused. They never send `error`, because the page turns any `error` into the end of the take.
4. **Server state is crash-safe:**
   - `Session` gets `paused: bool` and `pauses: [{at_frames, wall_paused_ms, open}]`. The pause start is kept with `time.monotonic()`.
   - Every pause and continue calls the atomic `write_meta`. `meta()` includes `pauses`, and `recover_all` restores them.
   - An open pause (End while paused, grace expiry, recovery) is closed in `finalize` with `open: true` and the elapsed time known so far.
   - `finalize` stores `source["pauses"]` next to `source["gaps"]`.
   - Pause **never touches** `writer.write/sync/close`.
   - `ready` after a `resume` carries `paused: true|false`. The page shows "Paused" or "Recording" from it and does **not** re-send pause.
5. **Page while paused:**
   - `onBlock` updates only the meter and `lastBlockAt`. It skips `blocks++`, the timer, the clip counter, the dead-mic counter and `queue.push`.
   - The `visibilitychange` gap check is guarded by `!paused`, and Continue resets `lastBlockAt`.
   - The state machine (labels, enable rules, sentinel order) lives as pure functions in `web/record/pause.js`, with a `module.exports` guard like `live.js`. It is loaded from `index.html` and tested with `node --test` through a pytest wrapper.
6. **Live assist:**
   - `LiveSession.pause()` puts a flush marker through `_q`, so the consumer calls `seg.flush()` in order after the queued chunks and handles its commit.
   - `segmenter.flush()` is fixed so that its short-buffer path also moves `base` (and `last_partial`/`_checked`) to the end. Without that, later timestamps would drift.
   - `Rolling` gets a `paused` flag that is checked after each sleep. An in-flight update may finish, but none **starts** while paused. Continue does not force an immediate update.
   - The page shows `live_state {state:"listening", message:"paused"}` as "Live: paused".
   - Pause or continue with live off or failed is a no-op and never raises.
7. **Tests that change:**
   - `tests/test_record_page.py`: the needle list adds `id="pause"`, `id="end"` and `/record/pause.js`.
   - `tests/e2e/test_live_brave.py`: the helpers stop with `#end` instead of a second `#mic` click.
   - `tests/js/live.test.mjs`: adds the paused message.
8. **Note:** the server still writes any binary frames it receives while paused. Pause positions are only meaningful for a well-behaved client, which this page is.
9. **Out of scope:** shortcuts, auto-pause on silence, and trimming pauses afterwards.

## Verification
- **WS tests:** start, 1 s audio, pause, 2 s of audio sent while "paused" from a misbehaving client, continue, 1 s audio, stop.
  - The server must accept `pause`/`continue` and store `source.pauses` with 1 entry.
  - The paused-period frames the server received are still written if a client sends them. The server trusts the client, and the page never sends them; the page test proves that.
- **Page JS test (node):** while paused, `onBlock` queues nothing, the timer doesn't advance, the button reads "Continue", and End is enabled.
- **Negative tests:**
  - `continue` without `pause` is ignored.
  - `pause` before `ready` → error "not recording".
  - The gap detector does not send `gap` during a 3 s pause.
- **Live:** pause flushes a commit (fake worker). The notes timer doesn't fire while paused.
- **Brave e2e:**
  - **Input:** play 6 s of the wav, pause for 3 s, continue for 4 s, then End.
  - **Expected:** the saved duration is about 10 s (±0.3 s, from the block count), not 13 s, and `source.pauses` has 1 entry.
  - **Screenshot:** the paused state, in light and dark.

## Caveats
- **The paused speech is truly gone:** nothing is buffered while paused. That is the point, but say it in the UI ("Paused: nothing is recorded").
- **The mic stays open while paused:** macOS shows the orange mic dot. The meter keeps showing your level so you can see the mic still works.
- **No trace in the audio:** the transcript and playback just jump over the paused time. The pause positions are stored for later (e.g. markers on the timeline in v2.x).
