"""Library: list/search, rename, trash/restore, audio path guard, and file import."""
import asyncio, pathlib, re, shutil, uuid

from aiohttp import web
from psycopg.types.json import Jsonb

from . import audio, recordings

MAX_TITLE = 200
DUR_TOLERANCE_MS = 250   # container durations of lossy sources are only approximate
COLS = "id, title, started_at, duration_ms, size_bytes, (SELECT count(*)::int FROM stt_run WHERE stt_run.recording_id = recording.id) AS runs_count, deleted_at"
CONTENT_TYPES = {"flac": "audio/flac", "wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg",
                 "m4a": "audio/mp4", "mp4": "audio/mp4"}


class ImportError_(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def as_uuid(s):
    try:
        return str(uuid.UUID(str(s)))
    except ValueError:
        return None


def item(row):
    return {**row, "id": str(row["id"]),
            "started_at": row["started_at"].isoformat(),
            "deleted_at": row["deleted_at"].isoformat() if row["deleted_at"] else None}


def _like(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def list_items(pool, q="", trash=False):
    where = ["deleted_at IS NOT NULL" if trash else "deleted_at IS NULL"]
    args = []
    if q:
        where.append("title ILIKE %s ESCAPE '\\'")
        args.append(_like(q))
    async with pool.connection() as c:
        cur = await c.execute(f"SELECT {COLS} FROM recording WHERE {' AND '.join(where)} "
                              "ORDER BY started_at DESC, created_at DESC", args)
        return [item(r) for r in await cur.fetchall()]


async def get_item(pool, rid):
    rid = as_uuid(rid)
    if not rid:
        return None
    async with pool.connection() as c:
        cur = await c.execute(f"SELECT {COLS} FROM recording WHERE id=%s", (rid,))
        row = await cur.fetchone()
    return item(row) if row else None


def clean_title(value):
    """-> trimmed title, or None if invalid (not a string, empty, > 200 chars)."""
    if not isinstance(value, str):
        return None
    t = value.strip()
    return t if 1 <= len(t) <= MAX_TITLE else None


async def _update(pool, rid, sql, args):
    rid = as_uuid(rid)
    if not rid:
        return None
    async with pool.connection() as c:
        cur = await c.execute(sql + f" RETURNING {COLS}", (*args, rid))
        row = await cur.fetchone()
    return item(row) if row else None


async def rename(pool, rid, title):
    return await _update(pool, rid, "UPDATE recording SET title=%s WHERE id=%s", (title,))


async def trash(pool, rid):
    return await _update(pool, rid, "UPDATE recording SET deleted_at=COALESCE(deleted_at, now()) WHERE id=%s", ())


async def restore(pool, rid):
    return await _update(pool, rid, "UPDATE recording SET deleted_at=NULL WHERE id=%s", ())


async def audio_file(pool, settings, rid):
    """-> (path, content_type) for a non-trashed recording; None if unknown/trashed;
    PermissionError if audio_path escapes <data_dir>/recordings/ or the file is missing."""
    rid = as_uuid(rid)
    if not rid:
        return None
    async with pool.connection() as c:
        cur = await c.execute("SELECT audio_path, container FROM recording WHERE id=%s AND deleted_at IS NULL", (rid,))
        row = await cur.fetchone()
    if not row:
        return None
    root = recordings.recordings_dir(settings).resolve()
    p = (settings.paths.data_dir / row["audio_path"]).resolve()
    if not p.is_relative_to(root):
        raise PermissionError("audio path outside the recordings folder")
    if not p.is_file():
        return None
    return p, CONTENT_TYPES.get(row["container"], "application/octet-stream")


# ---- import ---------------------------------------------------------------------------------------------

def _safe_name(filename):
    """-> (stem, ext) from an untrusted upload name: basename only, ext lowercase alphanumeric <= 8 chars."""
    base = pathlib.PurePosixPath((filename or "").replace("\\", "/")).name
    p = pathlib.PurePosixPath(base)
    ext = re.sub(r"[^a-z0-9]", "", p.suffix.lower())[:8]
    stem = p.stem.strip()[:MAX_TITLE].strip()
    return (stem or "Import"), (f".{ext}" if ext else ".bin")


def _preflight(settings, size):
    ff, fp = recordings.tools(settings)
    if not ff:
        raise ImportError_(503, "ffmpeg not found: install it (brew install ffmpeg) or set record.ffmpeg")
    if not fp:
        raise ImportError_(503, "ffprobe not found: install ffmpeg or set record.ffprobe")
    cap = settings.import_.max_mb * 1024 * 1024
    if size is not None and size > cap:
        raise ImportError_(413, f"file too large: limit is {settings.import_.max_mb} MB")
    d = settings.paths.data_dir
    d.mkdir(parents=True, exist_ok=True)
    need = 2 * (size if size is not None else cap)
    free = shutil.disk_usage(d).free
    if free < need:
        raise ImportError_(507, f"not enough disk space: {free / 1e9:.1f} GB free, {need / 1e9:.1f} GB required")
    return ff, fp, cap


def _convert(settings, ff, fp, src, dst):
    try:
        want = audio.probe(fp, src)["duration_s"] * 1000
        audio.normalise(ff, src, dst, settings.record.sample_rate)
        got = audio.probe(fp, dst)["duration_s"] * 1000
    except audio.AudioError as e:
        raise ImportError_(422, f"not a usable audio or video file: {e}") from None
    if abs(got - want) > DUR_TOLERANCE_MS:
        raise ImportError_(422, f"conversion check failed: duration {got:.0f} ms vs source {want:.0f} ms")
    return round(got), audio.sha256_file(dst), dst.stat().st_size


async def import_upload(pool, settings, reader, size):
    """reader: aiohttp MultipartReader. size: Content-Length of the request or None. Returns the list item.
    Any failure removes the new folder. ImportError_ carries the HTTP status."""
    ff, fp, cap = _preflight(settings, size)
    part = await reader.next()
    while part is not None and part.name != "file":
        part = await reader.next()
    if part is None:
        raise ImportError_(422, "no file in the upload (expected form field 'file')")
    stem, ext = _safe_name(part.filename)
    rid = str(uuid.uuid4())
    d = recordings.recordings_dir(settings) / rid
    try:
        d.mkdir(parents=True)
        src = d / f"original{ext}"
        total = 0
        with open(src, "wb") as f:
            while True:
                chunk = await part.read_chunk(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    raise ImportError_(413, f"file too large: limit is {settings.import_.max_mb} MB")
                f.write(chunk)
        if total == 0:
            raise ImportError_(422, "the uploaded file is empty")
        dur_ms, sha, fsize = await asyncio.to_thread(_convert, settings, ff, fp, src, d / "master.flac")
        async with pool.connection() as c:
            await c.execute(recordings.INSERT, (rid, stem, recordings_now(), dur_ms, f"recordings/{rid}/master.flac",
                                                sha, "flac", "flac", settings.record.sample_rate, 1, None,
                                                Jsonb({"import": "file", "original": src.name}), None, fsize))
        return await get_item(pool, rid)
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise


def recordings_now():
    import datetime
    return datetime.datetime.now().astimezone().isoformat()
