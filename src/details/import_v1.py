"""Import v1 sessions (sessions/<YYYYMMDD-HHMMSS>.wav + .txt) as recordings.
CLI: python -m details.import_v1 [--dir sessions] [--dry-run]"""
import argparse, datetime, pathlib, shutil, sys, uuid, zoneinfo

from psycopg.types.json import Jsonb

from .db import connect
from .services import audio, recordings
from .settings import REPO_ROOT, load_settings

STAMP = "%Y%m%d-%H%M%S"


def _started_at(path, tz):
    try:
        return datetime.datetime.strptime(path.stem, STAMP).replace(tzinfo=tz)
    except ValueError:
        return datetime.datetime.fromtimestamp(path.stat().st_mtime, tz)


def run(settings, src, dry_run=False, out=print):
    """Returns the exit code: 1 if any file failed, else 0."""
    src = pathlib.Path(src)
    ff, fp = recordings.tools(settings)
    tz = zoneinfo.ZoneInfo(settings.record.tz)
    results = []
    with connect(settings.db.url, settings.db.connect_timeout) as conn:
        for wav in sorted(src.glob("*.wav")):
            try:
                sha = audio.sha256_file(wav)
                if conn.execute("SELECT 1 FROM recording WHERE source->>'wav_sha256' = %s", (sha,)).fetchone():
                    results.append((wav.name, "skipped", "already imported"))
                    continue
                if not fp or not ff:
                    raise audio.AudioError("ffmpeg/ffprobe not found")
                info = audio.probe(fp, wav)      # raises on a corrupt file, before anything is written
                if dry_run:
                    results.append((wav.name, "would-import", f"{info['duration_s']:.1f} s"))
                    continue
                rid = str(uuid.uuid4())
                d = settings.paths.data_dir / "recordings" / rid
                d.mkdir(parents=True)
                try:
                    flac = d / "master.flac"
                    audio.to_flac(ff, wav, flac)
                    if (src / (wav.stem + ".txt")).is_file():
                        shutil.copyfile(src / (wav.stem + ".txt"), d / "v1-transcript.txt")
                    started = _started_at(wav, tz)
                    conn.execute(recordings.INSERT, (
                        rid, f"v1 session {started:%Y-%m-%d %H:%M}", started, round(info["duration_s"] * 1000),
                        f"recordings/{rid}/master.flac", audio.sha256_file(flac), "flac", "flac",
                        info["sample_rate"], info["channels"], Jsonb(["mic"]),
                        Jsonb({"import": "v1", "wav_sha256": sha}), None, flac.stat().st_size))
                except BaseException:
                    shutil.rmtree(d, ignore_errors=True)
                    raise
                results.append((wav.name, "imported", rid))
            except Exception as e:
                results.append((wav.name, "failed", f"{type(e).__name__}: {e}"[:120]))
    out(f"{'file':<24}{'result':<14}detail")
    for r in results:
        out(f"{r[0]:<24}{r[1]:<14}{r[2]}")
    n = lambda k: sum(1 for r in results if r[1] == k)
    out(f"imported {n('imported')}  would-import {n('would-import')}  skipped {n('skipped')}  failed {n('failed')}")
    return 1 if n("failed") else 0


def main():
    ap = argparse.ArgumentParser(prog="details.import_v1")
    ap.add_argument("--dir", default=str(REPO_ROOT / "sessions"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    s = load_settings()
    if not s.db.url:
        raise SystemExit("DATABASE_URL is not set (env or .env)")
    sys.exit(run(s, a.dir, a.dry_run))


if __name__ == "__main__":
    main()
