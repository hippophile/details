"""Live assist over the real /api/record/ws: messages, resume snapshot, live off, a raising live, Stop with a blocked
worker. The server runs the real live worker with the fake STT engine."""
import asyncio, time, wave

from details.app import LEASE
from details.live.session import LiveSession
from live_helpers import (WsLog, pid_alive, pids, read_calls, run_ws, script_pcm, wait_until, ws_send, ws_start)
from recording_helpers import SR, sine_pcm

PHRASES = script_pcm(("t", 1.0), ("s", 0.8), ("t", 1.0), ("s", 0.8))


def flac_ms(settings, rid):
    import subprocess
    from details.services import recordings
    _, fp = recordings.tools(settings)
    out = subprocess.run([fp, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(settings.paths.data_dir / "recordings" / rid / "master.flac")], capture_output=True, text=True)
    return float(out.stdout) * 1000


def test_live_take_streams_state_commits_and_saves_the_whole_audio(db, db_url, mk_settings, monkeypatch, tmp_path):
    async def fn(c, s, x):
        ws, log = await ws_start(c)
        await log.wait(lambda l: l.of("live_state", state="listening"), what="listening")
        await ws_send(ws, PHRASES)
        await log.wait(lambda l: len(l.of("live_commit")) >= 2, what="2 commits")
        assert c.app[LEASE].owner == "live"
        await ws.send_json({"type": "stop"})
        await log.wait(lambda l: l.of("saved"), what="saved")
        c1 = log.of("live_commit")[0]
        assert c1["text"].startswith("w") and c1["start_ms"] <= 100 and c1["idx"] == 0
        rid = log.of("saved")[0]["recording"]["id"]
        assert abs(flac_ms(s, rid) - 3600) <= 50
        assert c.app[LEASE].owner is None
        return log
    run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)


def test_live_off_sends_nothing_spawns_nothing_takes_no_lease(db, db_url, mk_settings, monkeypatch, tmp_path):
    async def fn(c, s, x):
        ws, log = await ws_start(c, live=None)
        assert c.app[LEASE].owner is None
        await ws_send(ws, PHRASES)
        await ws.send_json({"type": "stop"})
        await log.wait(lambda l: l.of("saved"))
        assert [m for m in log.msgs if m["type"].startswith("live_")] == []
        assert pids(x.pidfile) == [] and c.app[LEASE].owner is None
        ws2, log2 = await ws_start(c, live=False)
        assert c.app[LEASE].owner is None
        await ws_send(ws2, PHRASES)
        await ws2.send_json({"type": "stop"})
        await log2.wait(lambda l: l.of("saved"))
        assert [m for m in log2.msgs if m["type"].startswith("live_")] == [] and pids(x.pidfile) == []
    run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)


def test_model_not_installed_still_records_and_says_why(db, db_url, mk_settings, monkeypatch, tmp_path):
    async def fn(c, s, x):
        ws, log = await ws_start(c)
        await log.wait(lambda l: l.of("live_state", state="off"))
        assert "not installed" in log.of("live_state")[0]["message"]
        await ws_send(ws, PHRASES)
        await ws.send_json({"type": "stop"})
        await log.wait(lambda l: l.of("saved"))
        assert abs(log.of("saved")[0]["recording"]["duration_ms"] - 3600) <= 50
    run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn, hub=())


def test_offer_raising_never_stops_the_writer_and_live_is_marked_failed(db, db_url, mk_settings, monkeypatch, tmp_path):
    def boom(self, data, frames_before):
        raise RuntimeError("live bug")
    monkeypatch.setattr(LiveSession, "offer", boom)

    async def fn(c, s, x):
        ws, log = await ws_start(c)
        await ws_send(ws, sine_pcm(2.0))
        await log.wait(lambda l: l.of("live_state", state="failed"), what="failed state")
        assert "live" in log.of("live_state", state="failed")[0]["message"].lower()
        await ws_send(ws, sine_pcm(1.0))
        await ws.send_json({"type": "stop"})
        await log.wait(lambda l: l.of("saved"))
        assert abs(log.of("saved")[0]["recording"]["duration_ms"] - 3000) <= 50
        assert c.app[LEASE].owner is None
    run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)


def test_stop_with_a_blocked_worker_saves_in_under_3_seconds(db, db_url, mk_settings, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_STT_DELAY", "5")

    async def fn(c, s, x):
        ws, log = await ws_start(c)
        await log.wait(lambda l: l.of("live_state", state="listening"), what="listening")
        await ws_send(ws, script_pcm(("t", 1.0), ("s", 0.8), ("t", 3.0)))
        await wait_until(lambda: read_calls(x.calls), what="worker received a request")     # now blocked 5 s
        t0 = time.monotonic()
        await ws.send_json({"type": "stop"})
        await log.wait(lambda l: l.of("saved"), 10, "saved")
        took = time.monotonic() - t0
        assert took < 3.0, took
        rec = log.of("saved")[0]["recording"]
        assert abs(rec["duration_ms"] - 4800) <= 50 and abs(flac_ms(s, rec["id"]) - 4800) <= 50
        await wait_until(lambda: not any(pid_alive(p) for p in pids(x.pidfile)), 5, "child gone")
        assert c.app[LEASE].owner is None
        return took
    run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)


def test_resume_replays_committed_lines_in_a_snapshot(db, db_url, mk_settings, monkeypatch, tmp_path):
    async def fn(c, s, x):
        ws, log = await ws_start(c)
        await ws_send(ws, PHRASES)
        await log.wait(lambda l: len(l.of("live_commit")) >= 2, what="2 commits")
        rid = log.of("ready")[0]["recording_id"]
        await ws.close()                                           # connection drops: the take goes to grace
        await asyncio.sleep(0.3)
        assert c.app[LEASE].owner == "live"                        # the lease is NOT released at a plain close
        ws2 = await c.ws_connect("/api/record/ws")
        log2 = WsLog(ws2)
        await ws2.send_json({"type": "resume", "recording_id": rid})
        await log2.wait(lambda l: l.of("live_snapshot"), what="snapshot")
        order = [m["type"] for m in log2.msgs]
        assert order.index("ready") < order.index("live_snapshot")
        snap = log2.of("live_snapshot")[0]
        assert [ln["text"] for ln in snap["lines"]] == [m["text"] for m in log.of("live_commit")[:2]]
        await ws_send(ws2, sine_pcm(1.0))
        await ws2.send_json({"type": "stop"})
        await log2.wait(lambda l: l.of("saved"))
        assert c.app[LEASE].owner is None
    run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)


def test_lease_released_at_grace_expiry_not_before(db, db_url, mk_settings, monkeypatch, tmp_path):
    async def fn(c, s, x):
        ws, log = await ws_start(c)
        await ws_send(ws, PHRASES)
        await log.wait(lambda l: len(l.of("live_commit")) >= 2)
        await ws.close()
        await asyncio.sleep(0.4)
        assert c.app[LEASE].owner == "live"
        await wait_until(lambda: c.app[LEASE].owner is None, 8, "released at grace expiry")
        await wait_until(lambda: not any(pid_alive(p) for p in pids(x.pidfile)), 5, "child gone")
        await wait_until(lambda: db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 1, 8, "finalized")
        return s
    s = run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn, DETAILS_RECORD_GRACE_S="1")
    rows = db.execute("SELECT count(*) FROM recording").fetchone()[0]
    assert rows == 1
    assert db.execute("SELECT count(*) FROM stt_run WHERE settings->>'live_preview'='true'").fetchone()[0] == 1


def test_app_cleanup_releases_the_lease_and_kills_the_child(db, db_url, mk_settings, monkeypatch, tmp_path):
    holder = {}

    async def fn(c, s, x):
        ws, log = await ws_start(c)
        await log.wait(lambda l: l.of("live_state", state="listening"))
        holder["lease"], holder["x"] = c.app[LEASE], x
        assert holder["lease"].owner == "live"
    run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)       # the client context closes the app here
    assert holder["lease"].owner is None
    assert not any(pid_alive(p) for p in pids(holder["x"].pidfile))


def test_pause_over_the_websocket_flushes_a_commit_and_shows_paused(db, db_url, mk_settings, monkeypatch, tmp_path):
    async def fn(c, s, x):
        ws, log = await ws_start(c)
        await log.wait(lambda l: l.of("live_state", state="listening"), what="listening")
        await ws_send(ws, script_pcm(("t", 2.0)))
        await ws.send_json({"type": "pause"})
        await log.wait(lambda l: l.of("live_commit"), what="flush commit")
        await log.wait(lambda l: l.of("live_state", message="paused"), what="paused state")
        await ws.send_json({"type": "continue"})
        await log.wait(lambda l: l.of("live_state", message="listening"), what="listening again")
        await ws.send_json({"type": "stop"})
        await log.wait(lambda l: l.of("saved"), what="saved")
        assert not log.of("error")
    run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)
