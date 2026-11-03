import os, subprocess

import pytest

from library_helpers import make_media, need_ffmpeg, run_app
from recording_helpers import fake_tool

import aiohttp


def form(path, filename=None):
    f = aiohttp.FormData(quote_fields=False)
    f.add_field("file", open(path, "rb"), filename=filename or os.path.basename(path))
    return f


def rec_dirs(s):
    d = s.paths.data_dir / "recordings"
    return sorted(d.iterdir()) if d.exists() else []


def count(db):
    return db.execute("SELECT count(*) FROM recording").fetchone()[0]


def test_import_m4a(db, db_url, mk_settings, tmp_path):
    need_ffmpeg()
    src = make_media(tmp_path / "Standup 3 Nov.m4a")

    async def fn(c, s):
        r = await c.post("/api/recordings/import", data=form(src))
        return r.status, await r.json()
    st, j = run_app(mk_settings, fn)
    assert st == 201 and j["title"] == "Standup 3 Nov"
    s = mk_settings(db_url)
    (d,) = rec_dirs(s)
    assert (d / "original.m4a").read_bytes() == src.read_bytes()
    row = db.execute("SELECT id, title, duration_ms, sample_rate, channels, container, codec, audio_path, source, "
                     "size_bytes, audio_sha256 FROM recording").fetchone()
    assert str(row[0]) == d.name == j["id"] and abs(row[2] - 5000) <= 50
    assert row[3:7] == (16000, 1, "flac", "flac") and row[7] == f"recordings/{d.name}/master.flac"
    assert row[8] == {"import": "file", "original": "original.m4a"}
    assert row[9] == (d / "master.flac").stat().st_size and row[10]
    assert set(p.name for p in d.iterdir()) == {"original.m4a", "master.flac"}


def test_import_stereo_downmixed(db, db_url, mk_settings, tmp_path):
    need_ffmpeg()
    src = make_media(tmp_path / "st.m4a", stereo=True, seconds=2)

    async def fn(c, s):
        return (await c.post("/api/recordings/import", data=form(src))).status
    assert run_app(mk_settings, fn) == 201
    assert db.execute("SELECT channels, sample_rate FROM recording").fetchone() == (1, 16000)


def test_import_mp4_video_dropped_from_master(db, db_url, mk_settings, tmp_path):
    need_ffmpeg()
    src = make_media(tmp_path / "talk.mp4", kind="mp4")

    async def fn(c, s):
        return (await c.post("/api/recordings/import", data=form(src))).status
    assert run_app(mk_settings, fn) == 201
    s = mk_settings(db_url)
    (d,) = rec_dirs(s)
    assert (d / "original.mp4").read_bytes() == src.read_bytes()
    kinds = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            str(d / "master.flac")], capture_output=True, text=True).stdout.split()
    assert kinds == ["audio"]
    kinds = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            str(d / "original.mp4")], capture_output=True, text=True).stdout.split()
    assert "video" in kinds
    assert abs(db.execute("SELECT duration_ms FROM recording").fetchone()[0] - 5000) <= 100


def test_text_file_named_wav_422_no_leftovers(db, db_url, mk_settings, tmp_path):
    need_ffmpeg()
    src = tmp_path / "notes.wav"
    src.write_text("hello, this is not audio\n" * 50)

    async def fn(c, s):
        r = await c.post("/api/recordings/import", data=form(src))
        return r.status, (await r.json()).get("error", "")
    st, msg = run_app(mk_settings, fn)
    assert st == 422 and msg
    assert rec_dirs(mk_settings(db_url)) == [] and count(db) == 0


def test_oversize_413_cleanup(db, db_url, mk_settings, tmp_path):
    need_ffmpeg()
    src = tmp_path / "big.wav"
    src.write_bytes(os.urandom(2 * 1024 * 1024))

    async def fn(c, s):
        return (await c.post("/api/recordings/import", data=form(src))).status
    assert run_app(mk_settings, fn, DETAILS_IMPORT_MAX_MB="1") == 413
    assert rec_dirs(mk_settings(db_url)) == [] and count(db) == 0


def test_oversize_chunked_without_content_length_413(db, db_url, mk_settings, tmp_path):
    need_ffmpeg()

    async def gen():
        for _ in range(32):
            yield os.urandom(64 * 1024)

    async def fn(c, s):
        f = aiohttp.FormData(quote_fields=False)
        f.add_field("file", gen(), filename="big.wav", content_type="application/octet-stream")
        return (await c.post("/api/recordings/import", data=f)).status
    assert run_app(mk_settings, fn, DETAILS_IMPORT_MAX_MB="1") == 413
    assert rec_dirs(mk_settings(db_url)) == [] and count(db) == 0


def test_ffmpeg_missing_503(db, db_url, mk_settings, tmp_path):
    src = tmp_path / "a.m4a"
    src.write_bytes(b"x" * 100)

    async def fn(c, s):
        r = await c.post("/api/recordings/import", data=form(src))
        return r.status, (await r.json()).get("error", "")
    st, msg = run_app(mk_settings, fn, DETAILS_RECORD_FFMPEG=str(tmp_path / "nope"))
    assert st == 503 and "ffmpeg" in msg
    assert rec_dirs(mk_settings(db_url)) == [] and count(db) == 0


def test_low_disk_507_nothing_written(db, db_url, mk_settings, tmp_path, monkeypatch):
    need_ffmpeg()
    src = make_media(tmp_path / "a.m4a", seconds=2)
    size = src.stat().st_size
    from collections import namedtuple
    DU = namedtuple("DU", "total used free")
    from details.services import library
    monkeypatch.setattr(library.shutil, "disk_usage", lambda p: DU(10**12, 10**12 - 2 * size + 1, 2 * size - 1))

    async def fn(c, s):
        return (await c.post("/api/recordings/import", data=form(src))).status
    assert run_app(mk_settings, fn) == 507
    assert rec_dirs(mk_settings(db_url)) == [] and count(db) == 0


def test_enough_disk_exactly_2x_ok(db, db_url, mk_settings, tmp_path, monkeypatch):
    need_ffmpeg()
    src = make_media(tmp_path / "a.m4a", seconds=2)
    size = src.stat().st_size
    from collections import namedtuple
    DU = namedtuple("DU", "total used free")
    from details.services import library
    monkeypatch.setattr(library.shutil, "disk_usage", lambda p: DU(10**12, 0, 3 * size + 4096))

    async def fn(c, s):
        return (await c.post("/api/recordings/import", data=form(src))).status
    assert run_app(mk_settings, fn) == 201


def test_foreign_origin_rejected(db, db_url, mk_settings, tmp_path):
    src = tmp_path / "a.m4a"
    src.write_bytes(b"x" * 100)

    async def fn(c, s):
        return (await c.post("/api/recordings/import", data=form(src), headers={"Origin": "http://evil.example"})).status
    assert run_app(mk_settings, fn) == 403
    assert rec_dirs(mk_settings(db_url)) == [] and count(db) == 0


def test_unsafe_filename_sanitised(db, db_url, mk_settings, tmp_path):
    need_ffmpeg()
    src = make_media(tmp_path / "a.m4a", seconds=2)

    async def fn(c, s):
        r = await c.post("/api/recordings/import", data=form(src, filename="../../x.m4a"))
        return r.status, await r.json()
    st, j = run_app(mk_settings, fn)
    s = mk_settings(db_url)
    assert st == 201 and j["title"] == "x"
    (d,) = rec_dirs(s)
    assert (d / "original.m4a").exists()
    assert not (s.paths.data_dir.parent / "x.m4a").exists() and not (s.paths.data_dir / "x.m4a").exists()


def test_import_failure_after_convert_cleans_up(db, db_url, mk_settings, tmp_path):
    """ffmpeg fails mid-conversion (fake tool): 422/500, folder removed, no row."""
    need_ffmpeg()
    src = make_media(tmp_path / "a.m4a", seconds=2)
    bad = fake_tool(tmp_path / "ffmpeg", "echo boom >&2; exit 1")

    async def fn(c, s):
        return (await c.post("/api/recordings/import", data=form(src))).status
    st = run_app(mk_settings, fn, DETAILS_RECORD_FFMPEG=bad)
    assert st == 422
    assert rec_dirs(mk_settings(db_url)) == [] and count(db) == 0
