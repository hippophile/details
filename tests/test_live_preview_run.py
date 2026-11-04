"""The live preview saved as an stt_run at finalize, and its guard rails."""
import asyncio, types

import pytest

from details.services import recordings
from live_helpers import run_ws, script_pcm, ws_send, ws_start
from recording_helpers import SR, sine_pcm

THREE = script_pcm(("t", 1.0), ("s", 0.8), ("t", 1.0), ("s", 0.8), ("t", 1.0), ("s", 0.8))


async def take(c, pcm, want_commits=0, live=True):
    ws, log = await ws_start(c, live=live)
    await ws_send(ws, pcm)
    if want_commits:
        await log.wait(lambda l: len(l.of("live_commit")) >= want_commits, what=f"{want_commits} commits")
    await ws.send_json({"type": "stop"})
    await log.wait(lambda l: l.of("saved") or l.of("empty"), what="saved")
    return log


def test_three_commits_make_one_done_preview_run_with_segments(db, db_url, mk_settings, monkeypatch, tmp_path):
    async def fn(c, s, x):
        log = await take(c, THREE, 3)
        rid = log.of("saved")[0]["recording"]["id"]
        runs = await (await c.get(f"/api/recordings/{rid}/stt-runs")).json()
        return log, rid, runs
    log, rid, runs = run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)
    (r,) = db.execute("SELECT id, engine, model_id, status, text, audio_ms, settings, language, wall_ms, rtf "
                      "FROM stt_run WHERE recording_id=%s", (rid,)).fetchall()
    assert (r[1], r[2], r[3]) == ("mlx_whisper", "whisper-turbo", "done")
    assert r[7] == "el" and r[8] is None and r[9] is None and abs(r[5] - 5400) <= 50
    assert r[6]["live_preview"] is True and r[6]["dropped_ms"] == 0 and r[6]["silence_db"] == -45
    segs = db.execute("SELECT idx, start_ms, end_ms, text FROM transcript_segment WHERE stt_run_id=%s ORDER BY idx",
                      (r[0],)).fetchall()
    assert [x[0] for x in segs] == [0, 1, 2] and segs[1][1] > segs[0][2] - 50
    assert r[4] == " ".join(x[3] for x in segs) == " ".join(m["text"] for m in log.of("live_commit")[:3])
    assert "(live preview)" in runs[0]["label"] and runs[0]["label"].endswith("done")
    assert runs[0]["settings"]["live_preview"] is True
    assert (tmp_path / "data" / "recordings" / rid / "live" / "transcript.jsonl").exists()


def test_no_live_no_run(db, db_url, mk_settings, monkeypatch, tmp_path):
    async def fn(c, s, x):
        return await take(c, THREE, live=None)
    log = run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM recording").fetchone()[0] == 1


def test_live_take_without_commits_makes_no_run(db, db_url, mk_settings, monkeypatch, tmp_path):
    async def fn(c, s, x):
        return await take(c, script_pcm(("s", 2.0)))
    run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)
    assert db.execute("SELECT count(*) FROM recording").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 0


def test_too_short_take_saves_nothing_and_no_run(db, db_url, mk_settings, monkeypatch, tmp_path):
    async def fn(c, s, x):
        return await take(c, sine_pcm(0.3))
    log = run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)
    assert log.of("empty") and db.execute("SELECT count(*) FROM recording").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 0


def test_a_failing_preview_insert_never_fails_the_take(db, db_url, mk_settings, monkeypatch, tmp_path):
    async def boom(*a, **k):
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(recordings, "_insert_preview", boom)

    async def fn(c, s, x):
        return await take(c, THREE, 3)
    log = run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn)
    assert log.of("saved") and not log.of("error")
    assert db.execute("SELECT count(*) FROM recording").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 0


def test_label_suffix_is_only_for_live_preview_runs():
    from details.services import stt_runs
    reg = types.SimpleNamespace(stt={"whisper-turbo": types.SimpleNamespace(label="Whisper large-v3 turbo")})
    row = {"id": "i", "model_id": "whisper-turbo", "n": 2, "status": "done", "wall_ms": None, "rtf": None,
           "settings": {"live_preview": True}}
    assert stt_runs._out(dict(row), reg)["label"] == "#2 · Whisper large-v3 turbo (live preview) · done"
    assert stt_runs._out({**row, "settings": {}}, reg)["label"] == "#2 · Whisper large-v3 turbo · done"
