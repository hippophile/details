"""Step `diarize`: run the diarization worker child over the recording, then store the turns and the talk-time numbers
atomically. Finalizer `on_terminal`: a job that ends cancelled/failed (even while queued) sets its diarization_run to
match. The pool is bound at startup (`bind`): steps and finalizers get no pool from the runner.
The worker is started through `env -u <token names> HF_HUB_OFFLINE=1 HF_HUB_CACHE=<hub>` so it never sees a Hugging
Face token and never goes online (the model comes from the local cache)."""
import json, logging, re, sys, time

from ..services import diarization, library
from ..services.models import load_registry, resolve_path
from ..settings import SECRET_ENV
from .joblog import redact, secrets_from
from .steps import StepError, steps

log = logging.getLogger("details.diarize")
_POOL = _SETTINGS = None
PROGRESS_LINE = re.compile(r"^progress (\S+) (\d+)/(\d+)$")
ENV = "/usr/bin/env"


def bind(pool, *, settings=None):
    """settings (optional) adds the configured secrets to the redaction of error texts."""
    global _POOL, _SETTINGS
    _POOL, _SETTINGS = pool, settings


def _worker_error(step_dir, secrets):
    """The worker's own plain message (error.json), redacted, or None."""
    try:
        msg = json.loads((step_dir / "error.json").read_text(encoding="utf-8")).get("message")
    except (OSError, ValueError, AttributeError):
        return None
    return redact(str(msg), secrets) if msg else None


def _scrub_stderr(step_dir, secrets):
    """Mask token-like values in the child's stderr log in place (library code may print anything to stderr)."""
    path = step_dir / "child.stderr.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        clean = redact(text, secrets)
        if clean != text:
            path.write_text(clean, encoding="utf-8")
    except OSError:
        pass


async def diarize(ctx):
    pool, run_id = _POOL, library.as_uuid(ctx.params.get("diarization_run_id"))
    async with pool.connection() as c:
        row = await (await c.execute("SELECT recording_id, model_id, settings FROM diarization_run WHERE id=%s",
                                     (run_id,))).fetchone() if run_id else None
        if row is None:
            raise StepError("diarization run not found")
        await c.execute("UPDATE diarization_run SET status='running', started_at=COALESCE(started_at, now()), "
                        "error=NULL WHERE id=%s", (run_id,))
        rec = await (await c.execute("SELECT duration_ms FROM recording WHERE id=%s", (row["recording_id"],))).fetchone()
    found = await library.audio_file(pool, ctx.settings, str(row["recording_id"]))
    if rec is None or found is None:
        raise StepError("audio file not available")
    model = load_registry(resolve_path(ctx.settings)).diar.get(row["model_id"])
    if model is None:
        raise StepError(f"unknown model '{row['model_id']}'")
    d, rs, audio_ms = ctx.settings.diarization, row["settings"], rec["duration_ms"]
    t0 = time.monotonic()
    secrets = secrets_from(ctx.settings)
    spec = {"engine": model.engine, "model": {"repo": model.repo, "ffmpeg": ctx.settings.record.ffmpeg},
            "audio_path": str(found[0]), "device": rs.get("device") or d.device,
            "min_speakers": rs.get("min_speakers"), "max_speakers": rs.get("max_speakers"),
            "out_dir": str(ctx.step_dir), "hf_hub": str(ctx.settings.paths.hf_hub),
            "plugins": [m.strip() for m in d.plugins.split(",") if m.strip()], "keepalive_s": d.keepalive_s,
            "stall_s": d.stall_s, "progress_min_interval_s": d.progress_min_interval_s}
    spec_path = ctx.step_dir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    turns_path, done_path = ctx.step_dir / "turns.json", ctx.step_dir / "worker.done.json"
    ctx.progress(0.0, "Loading speaker model")

    def on_line(line):
        m = PROGRESS_LINE.match(line)
        if m:
            done, total = int(m.group(2)), int(m.group(3))
            if total:
                ctx.progress(done / total, f"Speakers: {m.group(1)} {done}/{total}")
        elif line.startswith("loaded"):
            ctx.progress(0.0, "Speaker model loaded")

    if not (turns_path.is_file() and done_path.is_file()):        # resume: a finished worker is never run twice
        (ctx.step_dir / "error.json").unlink(missing_ok=True)
        await ctx.memory.release_for_stt()
        argv = [ENV, *(a for name in SECRET_ENV for a in ("-u", name)), "HF_HUB_OFFLINE=1",
                f"HF_HUB_CACHE={ctx.settings.paths.hf_hub}", sys.executable, "-m", "details.engines.diar_worker",
                str(spec_path)]
        try:
            await ctx.spawn_child(argv, on_line)
        except StepError as e:
            msg = _worker_error(ctx.step_dir, secrets)
            if msg:
                raise StepError(msg) from None
            raise e
        finally:
            _scrub_stderr(ctx.step_dir, secrets)
        if ctx.cancelled.is_set():
            return None
    turns = json.loads(turns_path.read_text(encoding="utf-8"))["turns"]
    wall_ms = max(1, round((time.monotonic() - t0) * 1000))
    speakers = await diarization.store_result(pool, run_id, turns, audio_ms=audio_ms, wall_ms=wall_ms,
                                              merge_gap_ms=rs.get("merge_gap_ms", d.merge_gap_ms))
    return {"diarization_run_id": run_id, "speakers": len(speakers)}


async def finalize(job, status, error):
    if _POOL is None:
        log.warning("diarize finalizer ran before the pool was bound")
        return
    run_id = next((s["params"].get("diarization_run_id") for s in job["steps"] if s["name"] == "diarize"), None) \
        or (job.get("params") or {}).get("diarization_run_id")
    run_id = library.as_uuid(run_id)
    if not run_id:
        return
    msg = redact(error or ("cancelled" if status == "cancelled" else "failed"),
                 secrets_from(_SETTINGS) if _SETTINGS is not None else ())
    async with _POOL.connection() as c:
        await c.execute("UPDATE diarization_run SET status=%s, error=%s, finished_at=now() "
                        "WHERE id=%s AND status IN ('queued', 'running')", (status, msg, run_id))


def install(registry):
    registry.register("diarize")(diarize)
    registry.on_terminal("diarize")(finalize)


install(steps)
