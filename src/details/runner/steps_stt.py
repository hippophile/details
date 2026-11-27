"""Step `stt_transcribe`: run the engine worker child over the recording in windows, then store the run atomically.
Finalizer `on_terminal`: a job that ends cancelled/failed (even while queued) sets its stt_run to match.
The pool is bound at startup (`bind`): steps and finalizers get no pool from the runner."""
import json, logging, math, re, sys, time

from psycopg.types.json import Jsonb

from ..engines.worker import read_done, window_plan
from ..services import library, stt_runs
from ..stt_clean import clean
from ..services.models import load_registry, resolve_path
from .steps import StepError, steps

log = logging.getLogger("details.stt")
_POOL = None
WINDOW_LINE = re.compile(r"^window (\d+)/(\d+)")


def bind(pool):
    global _POOL
    _POOL = pool


async def stt_transcribe(ctx):
    pool, run_id = _POOL, ctx.params.get("stt_run_id")
    async with pool.connection() as c:
        row = await (await c.execute("SELECT recording_id, engine, model_id, settings FROM stt_run WHERE id=%s",
                                     (run_id,))).fetchone()
        if row is None:
            raise StepError("stt run not found")
        rec = await (await c.execute("SELECT duration_ms FROM recording WHERE id=%s", (row["recording_id"],))).fetchone()
    found = await library.audio_file(pool, ctx.settings, str(row["recording_id"]))
    if rec is None or found is None:
        raise StepError("audio file not available")
    model = load_registry(resolve_path(ctx.settings)).stt.get(row["model_id"])
    if model is None:
        raise StepError(f"unknown model '{row['model_id']}'")
    rs, audio_ms = row["settings"], rec["duration_ms"]
    t0 = time.monotonic()
    async with pool.connection() as c:
        await c.execute("UPDATE stt_run SET status='running', started_at=COALESCE(started_at, now()), error=NULL "
                        "WHERE id=%s", (run_id,))
    spec = {"engine": row["engine"], "model": {"repo": model.repo, **model.options}, "audio_path": str(found[0]),
            "duration_s": audio_ms / 1000, "window_s": rs.get("window_s") or ctx.settings.stt.window_s,
            "overlap_s": rs.get("overlap_s", 0),      # a run stored before overlap existed keeps hard windows
            "settings": {"language": rs.get("language", "el"), "initial_prompt": rs.get("initial_prompt"),
                         "condition_on_previous_text": False},
            "out_dir": str(ctx.step_dir),
            "plugins": [m.strip() for m in ctx.settings.stt.plugins.split(",") if m.strip()]}
    spec_path = ctx.step_dir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    ctx.progress(0.0, "Loading model")

    def on_line(line):
        m = WINDOW_LINE.match(line)
        if m:
            i, n = int(m.group(1)), int(m.group(2))
            ctx.progress(i / n, f"Transcribing window {i}/{n}")
        elif line.startswith("loaded"):
            ctx.progress(0.0, "Model loaded")

    await ctx.memory.release_for_stt()
    await ctx.spawn_child([sys.executable, "-m", "details.engines.worker", str(spec_path)], on_line)
    if ctx.cancelled.is_set():
        return None
    done = _finite(read_done(ctx.step_dir / "segments.jsonl"))
    plan = window_plan(spec["duration_s"], spec["window_s"])
    missing = [i for i, _, _ in plan if i not in done]
    if missing:
        raise StepError(f"worker finished without windows {missing[:5]}")
    wall_ms = max(1, round((time.monotonic() - t0) * 1000))
    got, lang_seen = [], None
    for i, _, _ in plan:
        w = done[i]
        lang = w.get("language") or (rs.get("language") if rs.get("language") != "auto" else None)
        lang_seen = lang_seen or lang
        for s in w["segments"]:
            got.append({**s, "text": stt_runs.greek_postfix(s["text"]) if lang == "el" else s["text"]})
    got, counts = clean(got, audio_ms, spec["window_s"], spec["overlap_s"])
    segs, texts = [], []
    for s in got:
        t = s["text"]
        segs.append((len(segs), s["start_ms"], s["end_ms"], t, s.get("avg_logprob"), s.get("no_speech_prob"),
                     Jsonb(s["words"]) if s.get("words") else None))
        texts.append(t.strip())
    raw = {"windows": [done[i] for i, _, _ in plan], "cleaned": counts}
    async with pool.connection() as c:
        async with c.transaction():
            await c.execute("DELETE FROM transcript_segment WHERE stt_run_id=%s", (run_id,))
            async with c.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO transcript_segment (stt_run_id, idx, start_ms, end_ms, text, avg_logprob, "
                    "no_speech_prob, words) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", [(run_id, *s) for s in segs])
            await c.execute("UPDATE stt_run SET status='done', finished_at=now(), wall_ms=%s, audio_ms=%s, rtf=%s, "
                            "text=%s, raw=%s, language=COALESCE(language, %s), error=NULL WHERE id=%s",
                            (wall_ms, audio_ms, audio_ms / wall_ms, " ".join(t for t in texts if t), Jsonb(raw),
                             lang_seen, run_id))
    return {"stt_run_id": run_id, "segments": len(segs)}


def _finite(x):
    """NaN/inf -> None, recursively: Whisper scores a lone "!" in silence as NaN and Postgres JSON rejects NaN."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _finite(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_finite(v) for v in x]
    return x


async def finalize(job, status, error):
    if _POOL is None:
        log.warning("stt finalizer ran before the pool was bound")
        return
    run_id = next((s["params"].get("stt_run_id") for s in job["steps"] if s["name"] == "stt_transcribe"), None) \
        or (job.get("params") or {}).get("stt_run_id")
    if not run_id:
        return
    msg = error or ("cancelled" if status == "cancelled" else "failed")
    async with _POOL.connection() as c:
        await c.execute("UPDATE stt_run SET status=%s, error=%s, finished_at=now() "
                        "WHERE id=%s AND status IN ('queued', 'running')", (status, msg, run_id))


def install(registry):
    registry.register("stt_transcribe")(stt_transcribe)
    registry.on_terminal("stt_transcribe")(finalize)


install(steps)
