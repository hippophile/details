import asyncio, uuid

from conftest import client_for
from details.app import create_app
from details.services import wavstream
from recording_helpers import SR, raw_wav_stale, read_json, sine_pcm


def make_folder(s, status, pcm, tail=b""):
    rid = str(uuid.uuid4())
    d = s.paths.data_dir / "recordings" / rid
    d.mkdir(parents=True)
    raw_wav_stale(d / "rec.wav", pcm, tail=tail)
    wavstream.write_meta(d, {"id": rid, "title": "Killed", "status": status, "sample_rate": SR,
                             "started_at": "2026-11-03T10:00:00+02:00", "script_id": None})
    return rid, d


def boot(s):
    async def go():
        async with client_for(create_app(s)):
            pass
    asyncio.run(go())


def test_killed_session_recovered(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid, d = make_folder(s, "recording", sine_pcm(3), tail=b"\x01")
    done_id, dd = make_folder(s, "done", sine_pcm(1))
    before = (dd / "meta.json").read_bytes()
    boot(s)
    r = db.execute("SELECT title, duration_ms, source FROM recording WHERE id=%s", (rid,)).fetchone()
    assert r[0] == "Killed (recovered)" and abs(r[1] - 3000) <= 50 and r[2]["recovered"] is True
    assert (d / "master.flac").exists() and read_json(d / "meta.json")["status"] == "recovered"
    assert (dd / "meta.json").read_bytes() == before
    assert db.execute("SELECT count(*) FROM recording").fetchone()[0] == 1


def test_zero_frame_folder_removed(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid, d = make_folder(s, "recording", b"")
    boot(s)
    assert not d.exists() and db.execute("SELECT count(*) FROM recording").fetchone()[0] == 0
