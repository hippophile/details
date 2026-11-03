"""Shared helpers for the library tests: seed recording rows + files, synthetic media via real ffmpeg."""
import asyncio, datetime, shutil, subprocess, uuid

from psycopg.types.json import Jsonb

from conftest import client_for
from details.app import create_app

FFMPEG = shutil.which("ffmpeg")


def need_ffmpeg():
    import pytest
    if not FFMPEG:
        pytest.skip("ffmpeg not installed")


def seed(db, settings, title="rec", started="2026-11-03T10:00:00+00:00", deleted_at=None, body=b"x" * 1000,
         audio_path=None, name="master.flac"):
    """Insert a recording row; write `body` to recordings/<id>/<name> unless audio_path is given."""
    rid = str(uuid.uuid4())
    d = settings.paths.data_dir / "recordings" / rid
    d.mkdir(parents=True)
    (d / name).write_bytes(body)
    db.execute("INSERT INTO recording (id,title,started_at,duration_ms,audio_path,container,codec,sample_rate,"
               "channels,source,size_bytes,deleted_at) VALUES (%s,%s,%s,1000,%s,'flac','flac',16000,1,%s,%s,%s)",
               (rid, title, started, audio_path or f"recordings/{rid}/{name}", Jsonb({"capture": "mic"}),
                len(body), deleted_at))
    return rid


def ago(days):
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)


def run_app(mk_settings, fn, **env):
    """Run fn(client, settings) against a fresh app. Takes the DB url from the env itself so that it never shows up
    in assertion introspection output."""
    from conftest import _test_url
    s = mk_settings(_test_url(), **env)

    async def go():
        async with client_for(create_app(s)) as c:
            return await fn(c, s)
    return asyncio.run(go())


def make_media(path, kind="m4a", seconds=5, stereo=False):
    """Real ffmpeg: sine audio (.m4a/.wav) or an mp4 with a video track + audio."""
    ch = "2" if stereo else "1"
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate=44100"]
    if kind == "mp4":
        cmd += ["-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={seconds}:r=10", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-shortest"]
    cmd += ["-ac", ch, str(path)]
    subprocess.run(cmd, check=True)
    return path
