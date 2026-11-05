import asyncio, json, os, wave

import pytest

from conftest import client_for
from details.app import create_app
from recording_helpers import SR, blocks, fake_tool, read_json, sine_pcm


def rec_dirs(settings):
    d = settings.paths.data_dir / "recordings"
    return sorted(d.iterdir()) if d.exists() else []


async def start(ws, **kw):
    await ws.send_json({"type": "start", "title": "t", "script_id": None, "sample_rate": SR, **kw})
    return await ws.receive_json()


async def send_audio(ws, seconds):
    for b in blocks(sine_pcm(seconds)):
        await ws.send_bytes(b)


def with_app(mk_settings, db_url, fn, **env):
    s = mk_settings(db_url, **env)
    async def go():
        async with client_for(create_app(s)) as c:
            return await fn(c, s)
    return asyncio.run(go())


def rows(db):
    return db.execute("SELECT id, audio_path, container, codec, sample_rate, channels, duration_ms, source, "
                      "script_id FROM recording").fetchall()


def test_start_stop_saves_flac_and_row(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        ready = await start(ws, title="My take")
        assert ready["type"] == "ready"
        await send_audio(ws, 2)
        await ws.send_json({"type": "stop"})
        return s, ready, await ws.receive_json()
    s, ready, saved = with_app(mk_settings, db_url, fn)
    assert saved["type"] == "saved" and saved["recording"]["id"] == ready["recording_id"]
    assert saved["recording"]["title"] == "My take" and abs(saved["recording"]["duration_ms"] - 2000) <= 50
    (r,) = rows(db)
    assert str(r[0]) == ready["recording_id"] and r[1] == f"recordings/{r[0]}/master.flac"
    assert (r[2], r[3], r[4], r[5]) == ("flac", "flac", SR, 1) and r[7]["capture"] == "mic"
    d = s.paths.data_dir / "recordings" / str(r[0])
    assert (d / "master.flac").exists() and not (d / "rec.wav").exists()
    assert read_json(d / "meta.json")["status"] == "done"


def test_script_id_stored(db, db_url, mk_settings):
    sid = db.execute("INSERT INTO script (slug,name,body_md,sha256,sort_order) VALUES ('a','a','b','c',1) "
                     "RETURNING id").fetchone()[0]
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        await start(ws, script_id=sid)
        await send_audio(ws, 1)
        await ws.send_json({"type": "stop"})
        return await ws.receive_json()
    with_app(mk_settings, db_url, fn)
    assert rows(db)[0][8] == sid


def test_keep_wav(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        await start(ws)
        await send_audio(ws, 1)
        await ws.send_json({"type": "stop"})
        await ws.receive_json()
        return s
    s = with_app(mk_settings, db_url, fn, DETAILS_RECORD_KEEP_WAV="true")
    d = rec_dirs(s)[0]
    assert (d / "rec.wav").exists() and (d / "master.flac").exists()


def test_wrong_sample_rate_rejected_no_folder(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        return s, await start(ws, sample_rate=48000)
    s, m = with_app(mk_settings, db_url, fn)
    assert m["type"] == "error" and rec_dirs(s) == []


def test_short_take_discarded(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        await start(ws)
        await ws.send_bytes(sine_pcm(0.2))
        await ws.send_json({"type": "stop"})
        return s, await ws.receive_json()
    s, m = with_app(mk_settings, db_url, fn)
    assert m["type"] == "empty" and rec_dirs(s) == [] and rows(db) == []


def test_drop_then_resume_inside_grace(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        rid = (await start(ws))["recording_id"]
        await send_audio(ws, 1)
        await ws.close()
        await asyncio.sleep(0.2)
        ws2 = await c.ws_connect("/api/record/ws")
        await ws2.send_json({"type": "resume", "recording_id": rid})
        assert (await ws2.receive_json())["type"] == "ready"
        await send_audio(ws2, 1)
        await ws2.send_json({"type": "stop"})
        return await ws2.receive_json()
    m = with_app(mk_settings, db_url, fn, DETAILS_RECORD_GRACE_S="30")
    assert m["type"] == "saved" and abs(m["recording"]["duration_ms"] - 2000) <= 50


def test_drop_without_resume_finalizes_after_grace(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        rid = (await start(ws))["recording_id"]
        await send_audio(ws, 1)
        await ws.close()
        await asyncio.sleep(1.5)
        ws2 = await c.ws_connect("/api/record/ws")
        await ws2.send_json({"type": "resume", "recording_id": rid})
        return s, rid, await ws2.receive_json()
    s, rid, m = with_app(mk_settings, db_url, fn, DETAILS_RECORD_GRACE_S="0.2")
    (r,) = rows(db)
    assert r[7]["interrupted"] is True
    assert read_json(s.paths.data_dir / "recordings" / rid / "meta.json")["status"] == "done"
    assert m["type"] == "error" and "already finalized" in m["message"]


def test_ffmpeg_missing_refuses_start(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        return s, await start(ws)
    s, m = with_app(mk_settings, db_url, fn, DETAILS_RECORD_FFMPEG="/nonexistent/ffmpeg")
    assert m["type"] == "error" and "ffmpeg" in m["message"] and rec_dirs(s) == []


def test_ffmpeg_failure_keeps_wav_and_row_points_at_it(db, db_url, mk_settings, tmp_path):
    bad = fake_tool(tmp_path / "ffmpeg", "exit 1")
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        await start(ws)
        await send_audio(ws, 1)
        await ws.send_json({"type": "stop"})
        return await ws.receive_json()
    m = with_app(mk_settings, db_url, fn, DETAILS_RECORD_FFMPEG=bad)
    assert m["type"] == "saved" and m["recording"].get("warning")
    (r,) = rows(db)
    assert r[1].endswith("rec.wav") and (r[2], r[3]) == ("wav", "pcm_s16le") and r[7]["flac_failed"] is True


def test_low_disk_refused(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        return s, await start(ws)
    s, m = with_app(mk_settings, db_url, fn, DETAILS_RECORD_MIN_FREE_GB="100000000")
    assert m["type"] == "error" and "GB" in m["message"] and rec_dirs(s) == []


def test_foreign_origin_rejected(db, db_url, mk_settings):
    async def fn(c, s):
        try:
            await c.ws_connect("/api/record/ws", headers={"Origin": "http://evil.example"})
            ok = True
        except Exception:
            ok = False
        return s, ok
    s, ok = with_app(mk_settings, db_url, fn)
    assert not ok and rec_dirs(s) == []


def test_oversized_message_closes_socket_take_recoverable(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws", max_msg_size=0)
        await start(ws)
        await send_audio(ws, 1)
        try:
            await ws.send_bytes(b"\0" * (2 * 1024 * 1024))
        except ConnectionError:
            pass                               # server already closed the socket
        msg = await ws.receive()
        await asyncio.sleep(0.2)
        return s, msg
    s, msg = with_app(mk_settings, db_url, fn, DETAILS_RECORD_MAX_MSG_MB="1", DETAILS_RECORD_GRACE_S="60")
    assert msg.type.name in ("CLOSE", "CLOSING", "CLOSED")
    d = rec_dirs(s)[0]
    assert read_json(d / "meta.json")["status"] == "recording"
    with wave.open(str(d / "rec.wav")) as w:   # header already patched or repairable
        pass


# ---- record-pause: pause / continue ------------------------------------------------------------------------------

async def wait_meta(d, pred, what="meta"):
    for _ in range(100):
        m = read_json(d / "meta.json")
        if pred(m):
            return m
        await asyncio.sleep(0.03)
    raise AssertionError(f"timed out waiting for {what}")


def test_pause_continue_stores_one_pause(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        await start(ws)
        await send_audio(ws, 1)
        await ws.send_json({"type": "pause"})
        d = rec_dirs(s)[0]
        m = await wait_meta(d, lambda m: m.get("pauses"), "pause in meta")
        assert m["pauses"][0]["at_frames"] == SR and m["pauses"][0]["open"] is True
        await send_audio(ws, 2)                   # a misbehaving client: the server trusts it and writes it
        await ws.send_json({"type": "continue"})
        m = await wait_meta(d, lambda m: m["pauses"] and not m["pauses"][0].get("open"), "pause closed")
        assert m["pauses"][0]["wall_paused_ms"] >= 0
        await send_audio(ws, 1)
        await ws.send_json({"type": "stop"})
        return await ws.receive_json()
    saved = with_app(mk_settings, db_url, fn)
    assert saved["type"] == "saved" and abs(saved["recording"]["duration_ms"] - 4000) <= 50
    (r,) = rows(db)
    (p,) = r[7]["pauses"]
    assert p["at_frames"] == SR and "open" not in p and p["wall_paused_ms"] >= 0


def test_duplicate_pause_is_idempotent_and_never_an_error(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        await start(ws)
        await send_audio(ws, 1)
        await ws.send_json({"type": "pause"})
        await ws.send_json({"type": "pause"})
        await ws.send_json({"type": "continue"})
        await ws.send_json({"type": "continue"})
        await send_audio(ws, 1)
        await ws.send_json({"type": "stop"})
        return await ws.receive_json()               # an `error` would arrive first
    assert with_app(mk_settings, db_url, fn)["type"] == "saved"
    assert len(rows(db)[0][7]["pauses"]) == 1


def test_continue_without_pause_and_no_session_are_ignored(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        await ws.send_json({"type": "pause"})        # before start: no session
        await ws.send_json({"type": "continue"})
        ready = await start(ws)
        assert ready["type"] == "ready"              # not an error
        await ws.send_json({"type": "continue"})     # not paused
        await send_audio(ws, 1)
        await ws.send_json({"type": "stop"})
        return await ws.receive_json()
    assert with_app(mk_settings, db_url, fn)["type"] == "saved"
    assert "pauses" not in rows(db)[0][7]


def test_end_while_paused_closes_the_pause_open(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        await start(ws)
        await send_audio(ws, 1)
        await ws.send_json({"type": "pause"})
        await ws.send_json({"type": "stop"})
        return await ws.receive_json()
    saved = with_app(mk_settings, db_url, fn)
    assert saved["type"] == "saved" and abs(saved["recording"]["duration_ms"] - 1000) <= 50
    (p,) = rows(db)[0][7]["pauses"]
    assert p["open"] is True and p["at_frames"] == SR and p["wall_paused_ms"] >= 0


def test_ready_after_resume_carries_paused(db, db_url, mk_settings):
    async def fn(c, s):
        ws = await c.ws_connect("/api/record/ws")
        rid = (await start(ws))["recording_id"]
        await send_audio(ws, 1)
        await ws.send_json({"type": "pause"})
        await wait_meta(rec_dirs(s)[0], lambda m: m.get("pauses"))
        await ws.close()
        await asyncio.sleep(0.2)
        ws2 = await c.ws_connect("/api/record/ws")
        await ws2.send_json({"type": "resume", "recording_id": rid})
        r1 = await ws2.receive_json()
        await ws2.send_json({"type": "continue"})
        await send_audio(ws2, 1)
        await ws2.close()
        await asyncio.sleep(0.2)
        ws3 = await c.ws_connect("/api/record/ws")
        await ws3.send_json({"type": "resume", "recording_id": rid})
        r2 = await ws3.receive_json()
        await ws3.send_json({"type": "stop"})
        return r1, r2, await ws3.receive_json()
    r1, r2, saved = with_app(mk_settings, db_url, fn, DETAILS_RECORD_GRACE_S="30")
    assert r1["type"] == "ready" and r1["paused"] is True
    assert r2["type"] == "ready" and r2["paused"] is False
    assert saved["type"] == "saved" and len(rows(db)[0][7]["pauses"]) == 1
