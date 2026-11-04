"""Recording sessions: create, finalize (WAV -> FLAC master + row), startup recovery."""
import asyncio, datetime, logging, pathlib, shutil, uuid
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from . import audio, wavstream

log = logging.getLogger("details.recordings")
MIN_TAKE_S = 0.5
DUR_TOLERANCE_MS = 50
INSERT = ("INSERT INTO recording (id, title, started_at, duration_ms, audio_path, audio_sha256, container, codec, "
          "sample_rate, channels, channel_map, source, script_id, size_bytes) "
          "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")


class PreflightError(RuntimeError):
    pass


@dataclass
class Session:
    id: str
    dir: pathlib.Path
    title: str
    script_id: int | None
    started_at: str
    sample_rate: int
    writer: wavstream.WavWriter | None = None
    state: str = "recording"          # recording | interrupted | finalizing | finalized
    gaps: list = field(default_factory=list)
    grace_task: asyncio.Task | None = None
    attached: bool = False
    live: object | None = None        # live-assistant: a live.session.LiveSession (its commits become a preview stt_run)
    result: dict | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def meta(self, status):
        return {"id": self.id, "title": self.title, "script_id": self.script_id, "started_at": self.started_at,
                "sample_rate": self.sample_rate, "status": status, "gaps": self.gaps}


def recordings_dir(settings):
    return settings.paths.data_dir / "recordings"


def tools(settings):
    r = settings.record
    return audio.find_tool(r.ffmpeg, "ffmpeg"), audio.find_tool(r.ffprobe, "ffprobe")


def preflight(settings):
    ff, fp = tools(settings)
    if not ff:
        raise PreflightError("ffmpeg not found: install it (brew install ffmpeg) or set record.ffmpeg")
    if not fp:
        raise PreflightError("ffprobe not found: install ffmpeg or set record.ffprobe")
    d = settings.paths.data_dir
    d.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(d).free / 1e9
    if free < settings.record.min_free_gb:
        raise PreflightError(f"not enough disk space: {free:.1f} GB free, {settings.record.min_free_gb:g} GB required")


def create_session(settings, title, script_id, sample_rate):
    if sample_rate != settings.record.sample_rate:
        raise PreflightError(f"sample_rate {sample_rate} not supported; server requires {settings.record.sample_rate}")
    preflight(settings)
    rid = str(uuid.uuid4())
    d = recordings_dir(settings) / rid
    d.mkdir(parents=True)
    now = datetime.datetime.now().astimezone()
    s = Session(rid, d, title or f"Recording {now:%Y-%m-%d %H:%M}", script_id, now.isoformat(), sample_rate)
    s.writer = wavstream.WavWriter(d / "rec.wav", sample_rate, settings.record.sync_interval_s)
    wavstream.write_meta(d, s.meta("recording"))
    return s


def _convert(settings, wav, frames, sr):
    """-> (ok, error). Converts, then checks FLAC duration against the WAV data length."""
    ff, fp = tools(settings)
    flac = wav.with_name("master.flac")
    try:
        if not ff or not fp:
            raise audio.AudioError("ffmpeg/ffprobe not found")
        audio.to_flac(ff, wav, flac)
        got = audio.probe(fp, flac)["duration_s"] * 1000
        want = frames * 1000 / sr
        if abs(got - want) > DUR_TOLERANCE_MS:
            raise audio.AudioError(f"duration mismatch: flac {got:.0f} ms vs wav {want:.0f} ms")
        return True, None
    except audio.AudioError as e:
        flac.unlink(missing_ok=True)
        return False, str(e)


async def _insert_preview(app_pool, s, pv, duration_ms):
    """The live commits as one done stt_run (+ one transcript_segment per commit), in one transaction."""
    from . import stt_runs
    lines = [(l["idx"], l["start_ms"], l["end_ms"], stt_runs.greek_postfix(l["text"])) for l in pv["lines"]]
    run_settings = {"live_preview": True, "dropped_ms": pv["dropped_ms"], "silence_db": pv["silence_db"]}
    async with app_pool.connection() as c:
        async with c.transaction():
            cur = await c.execute(
                "INSERT INTO stt_run (recording_id, engine, model_id, settings, language, status, started_at, "
                "finished_at, audio_ms, text) VALUES (%s,%s,%s,%s,%s,'done',%s,now(),%s,%s) RETURNING id",
                (s.id, pv["engine"], pv["model_id"], Jsonb(run_settings), pv["language"], s.started_at, duration_ms,
                 " ".join(t for _, _, _, t in lines)))
            run_id = (await cur.fetchone())["id"]
            async with c.cursor() as cur:
                await cur.executemany("INSERT INTO transcript_segment (stt_run_id, idx, start_ms, end_ms, text) "
                                      "VALUES (%s,%s,%s,%s,%s)", [(run_id, *l) for l in lines])


async def finalize(app_pool, settings, s, *, recovered=False, interrupted=False):
    """Idempotent. Returns the recording dict, or None if the take was too short (folder removed)."""
    async with s.lock:
        if s.state == "finalized":
            return s.result
        s.state = "finalizing"
        if s.writer:
            s.writer.close()
        wav = s.dir / "rec.wav"
        frames = await asyncio.to_thread(wavstream.repair, wav) if wav.exists() else 0
        min_frames = 1 if recovered else int(MIN_TAKE_S * s.sample_rate)
        if frames < min_frames:
            shutil.rmtree(s.dir, ignore_errors=True)
            s.state, s.result = "finalized", None
            return None
        wavstream.write_meta(s.dir, s.meta("finalizing"))
        ok, err = await asyncio.to_thread(_convert, settings, wav, frames, s.sample_rate)
        source = {"capture": "mic"}
        if interrupted:
            source["interrupted"] = True
        if recovered:
            source["recovered"] = True
        if s.gaps:
            source["gaps"] = s.gaps
        title = s.title + (" (recovered)" if recovered else "")
        if ok:
            master = s.dir / "master.flac"
            sha = await asyncio.to_thread(audio.sha256_file, master)
            if not settings.record.keep_wav:
                wav.unlink(missing_ok=True)
            name, container, codec = "master.flac", "flac", "flac"
        else:
            source["flac_failed"] = True
            sha = await asyncio.to_thread(audio.sha256_file, wav)
            name, container, codec = "rec.wav", "wav", "pcm_s16le"
        path = s.dir / name
        duration_ms = round(frames * 1000 / s.sample_rate)
        size = path.stat().st_size
        preview = None
        if s.live is not None:
            try:
                preview = s.live.preview()
            except Exception:
                log.exception("live preview unavailable for %s", s.id)
        async with app_pool.connection() as c:
            await c.execute(INSERT, (s.id, title, s.started_at, duration_ms, f"recordings/{s.id}/{name}", sha,
                                     container, codec, s.sample_rate, 1, Jsonb(["mic"]), Jsonb(source),
                                     s.script_id, size))
        if preview:
            try:
                await _insert_preview(app_pool, s, preview, duration_ms)
            except Exception:                     # the take is saved; a missing preview run is only a convenience
                log.exception("live preview run not saved for %s", s.id)
        wavstream.write_meta(s.dir, s.meta("recovered" if recovered else "done"))
        s.state = "finalized"
        s.result = {"id": s.id, "title": title, "duration_ms": duration_ms, "size_bytes": size}
        if not ok:
            s.result["warning"] = f"FLAC conversion failed ({err}); the WAV was kept"
        return s.result


async def recover_all(app_pool, settings):
    """Startup: finalize every folder whose meta says recording/finalizing."""
    base = recordings_dir(settings)
    if not base.is_dir():
        return []
    out = []
    for d in sorted(base.iterdir()):
        meta = wavstream.read_meta(d)
        if not meta or meta.get("status") not in ("recording", "finalizing"):
            continue
        try:
            uuid.UUID(meta["id"])
            sess = Session(meta["id"], d, meta.get("title", "Recording"), meta.get("script_id"),
                           meta["started_at"], int(meta.get("sample_rate", settings.record.sample_rate)),
                           gaps=meta.get("gaps", []))
        except (KeyError, ValueError):
            continue
        async with app_pool.connection() as c:  # a crash after the insert but before meta=done
            cur = await c.execute("SELECT 1 FROM recording WHERE id=%s", (sess.id,))
            if await cur.fetchone():
                wavstream.write_meta(d, sess.meta("recovered"))
                continue
        res = await finalize(app_pool, settings, sess, recovered=True)
        out.append(res)
    return out
