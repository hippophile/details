"""Step `analyse`: one LM Studio call chain per output kind (validate, retry once, verify quotes, map-reduce for long
transcripts), persisted as analysis_run + output rows. Finalizer `on_terminal`: a job that ends cancelled/failed (even
while queued) sets its unfinished analysis_run rows to match and, with guards, unloads the model this app loaded.
The pool (and memory/settings/lease) are bound at startup (`bind`)."""
import hashlib, logging, types

from psycopg.types.json import Jsonb

from ..llm import client, pipeline
from ..llm.client import LLMError, StreamCancelled
from ..llm.lmstudio_ctl import identifier_for
from ..llm.prompts import language_name
from .joblog import JobLogs
from .steps import StepError, steps

log = logging.getLogger("details.analyse")
_POOL = _MEMORY = _SETTINGS = _HUB = _LEASE = None
RUN_COLS = ("group_id, stt_run_id, kind, prompt_template_id, llm_endpoint, model_id, params, output_language, "
            "input_text_sha256")


def bind(pool, *, memory=None, settings=None, hub=None, lease=None):
    """memory/settings/lease enable the terminal unload; `hub` (llm.stream_hub.StreamHub) receives the live reply
    text of the running step (llm-streaming); None = no live view."""
    global _POOL, _MEMORY, _SETTINGS, _HUB, _LEASE
    _POOL, _MEMORY, _SETTINGS, _HUB, _LEASE = pool, memory, settings, hub, lease


def _analyse_params(job):
    return next((s["params"] for s in job["steps"] if s["name"] == "analyse"), {})


async def _finished(c, gid, kind):
    """True when this kind already has a usable result (valid, or finally invalid after its retry)."""
    cur = await c.execute("SELECT 1 FROM analysis_run WHERE group_id=%s AND kind=%s AND status='done' AND "
                          "(schema_valid OR retry_of IS NOT NULL OR params->>'mode'='map-reduce') LIMIT 1",
                          (gid, kind))
    return await cur.fetchone() is not None


async def _persist(c, first_id, kind, result):
    """Write the attempts (first reuses the queued row, a retry is a new row linked by retry_of) and the output."""
    prev, last_id = None, first_id
    for i, a in enumerate(result.attempts):
        if i == 0:
            rid = first_id
        else:
            cur = await c.execute(f"INSERT INTO analysis_run ({RUN_COLS}, retry_of, status, started_at) "
                                  f"SELECT {RUN_COLS}, %s, 'running', now() FROM analysis_run WHERE id=%s RETURNING id",
                                  (prev, first_id))
            rid = (await cur.fetchone())["id"]
        tps = a.tokens_out / (a.wall_ms / 1000) if a.wall_ms > 0 and a.tokens_out else None
        extra = {"mode": result.mode, "chunks": result.chunks, "cache_hits": result.cache_hits,
                 "verified": result.verified, "unverified": result.unverified}
        err = None if a.valid else f"model returned invalid JSON: {a.errors[0]}"
        await c.execute(
            "UPDATE analysis_run SET status='done', finished_at=now(), wall_ms=%s, tokens_in=%s, tokens_out=%s, "
            "tok_per_s=%s, finish_reason=%s, raw_response=%s, schema_valid=%s, validation_errors=%s, "
            "rendered_prompt=%s, error=%s, params=params || %s WHERE id=%s",
            (a.wall_ms, a.tokens_in, a.tokens_out, tps, a.finish_reason, Jsonb({"text": a.text, "response": a.raw}),
             a.valid, None if a.valid else Jsonb(a.errors), a.rendered_prompt, err, Jsonb(extra), rid))
        prev = last_id = rid
    if result.content is not None:
        await c.execute("INSERT INTO output (analysis_run_id, kind, schema_version, content) VALUES (%s,%s,1,%s)",
                        (last_id, kind, Jsonb(result.content)))


async def analyse(ctx):
    try:
        return await _analyse(ctx)
    finally:
        if _HUB is not None:
            _HUB.end_job(ctx.job_id)          # the live text is not kept after the step


async def _analyse(ctx):
    pool, p = _POOL, ctx.params
    gid = p.get("group_id")
    async with pool.connection() as c:
        rows = await (await c.execute("SELECT a.id, a.kind, a.prompt_template_id, a.output_language, a.stt_run_id, "
                                      "t.body, t.sha256, t.checks FROM analysis_run a LEFT JOIN prompt_template t "
                                      "ON t.id=a.prompt_template_id WHERE a.group_id=%s AND a.retry_of IS NULL "
                                      "ORDER BY a.created_at, a.id", (gid,))).fetchall()
        if not rows:
            raise StepError("analysis not found")
        run = await (await c.execute("SELECT s.language, s.text, r.title FROM stt_run s JOIN recording r "
                                     "ON r.id=s.recording_id WHERE s.id=%s", (rows[0]["stt_run_id"],))).fetchone()
        segs = [r["text"].strip() for r in await (await c.execute(
            "SELECT text FROM transcript_segment WHERE stt_run_id=%s ORDER BY idx", (rows[0]["stt_run_id"],))).fetchall()]
    segs = [s for s in segs if s] or ([run["text"].strip()] if run and run["text"] and run["text"].strip() else [])
    if not segs:
        raise StepError("the transcript is empty")
    full = "\n".join(segs)
    input_sha = hashlib.sha256(full.encode("utf-8")).hexdigest()
    entry = ctx.memory.registry.llm.get(p.get("model_id"))
    if entry is None:
        raise StepError(f"unknown model_id '{p.get('model_id')}'")
    model_name = identifier_for(entry.id)
    language = language_name(run["language"], full, rows[0]["output_language"])

    await ctx.memory.ensure_llm(entry)       # no-op when loaded; a retry after the terminal unload reloads it
    totals = {"tokens_in": 0, "tokens_out": 0, "wall_ms": 0}

    hub, jid = _HUB, ctx.job_id
    plain_models = set()          # models whose streaming request was refused: plain calls for the rest of the job
    live = {"chunk": None, "last": {}}

    def on_event(typ, *a, **kw):
        if typ == "chunk":
            live["chunk"] = (a[0], a[1]) if a[0] else None
        elif typ == "attempt_end" and hub is not None:
            hub.end_attempt(jid, valid=kw["valid"], finish_reason=live["last"].get("finish_reason"),
                            tokens_out=live["last"].get("tokens_out"))

    async def chat(messages, *, kind, schema, max_tokens):
        attempt = 2 if any(m.get("role") == "assistant" for m in messages) else 1     # a retry appends the bad reply
        info = {"model": model_name, "kind": kind, "attempt": attempt}
        args = dict(model=model_name, kind=kind, schema=schema, messages=messages, max_tokens=max_tokens)
        streamed, fb = False, None
        if hub is not None:
            hub.start_attempt(jid, kind, attempt, live["chunk"])
        try:
            if ctx.settings.llm.stream and model_name not in plain_models:
                try:
                    r = await client.chat_stream(ctx.settings, **args, cancelled=ctx.cancelled,
                                                 on_delta=(lambda t: hub.delta(jid, t)) if hub is not None else None)
                    streamed = True
                except client.StreamUnsupported as u:
                    plain_models.add(model_name)
                    fb = u.reason
                    if hub is not None:
                        hub.fallback(jid, fb)
                    r = await client.chat(ctx.settings, **args)
            else:
                r = await client.chat(ctx.settings, **args)
        except StreamCancelled:
            ctx.log("warn", "LM Studio call cancelled", src="llm", **info, stream=True, status="cancelled")
            if hub is not None:
                hub.end_attempt(jid, valid=None, cancelled=True)
            raise
        except LLMError as e:
            ctx.log("error", "LM Studio call failed", src="llm", **info, tokens_in=None, tokens_out=None,
                    wall_ms=None, finish_reason=None, status=e.status, stream=streamed or None, fallback=fb)
            if hub is not None:
                hub.end_attempt(jid, valid=None, error=True)
            raise
        live["last"] = r
        ctx.log("info", "LM Studio call", src="llm", **info, tokens_in=r["tokens_in"], tokens_out=r["tokens_out"],
                wall_ms=r["wall_ms"], finish_reason=r["finish_reason"], status="ok", stream=streamed,
                ttft_ms=r["raw"].get("ttft_ms") if streamed else None, fallback=fb)
        for k in totals:
            totals[k] += r[k]
        ctx.metrics(model=model_name, tokens_in=totals["tokens_in"], tokens_out=totals["tokens_out"],
                    tok_per_s=round(totals["tokens_out"] / (totals["wall_ms"] / 1000), 1) if totals["wall_ms"] else None)
        return r

    for n, row in enumerate(rows):
        kind = row["kind"]
        async with pool.connection() as c:
            if await _finished(c, gid, kind):
                continue
            await c.execute("DELETE FROM analysis_run WHERE group_id=%s AND kind=%s AND retry_of IS NOT NULL",
                            (gid, kind))
            await c.execute("UPDATE analysis_run SET status='running', started_at=now(), finished_at=NULL, error=NULL, "
                            "schema_valid=NULL, validation_errors=NULL, raw_response=NULL, input_text_sha256=%s "
                            "WHERE id=%s", (input_sha, row["id"]))
        ctx.progress(n / len(rows), f"Writing {kind} ({n + 1}/{len(rows)})")
        template = types.SimpleNamespace(body=row["body"], sha256=row["sha256"], checks=tuple(row["checks"] or ()))
        live["chunk"] = None

        def on_progress(frac, msg=""):
            ctx.progress((n + frac) / len(rows), msg)
        try:
            result = await pipeline.generate_kind(chat, settings=ctx.settings, kind=kind, template=template,
                                                  model=model_name, context=entry.context, segments=segs,
                                                  language=language, title=run["title"], data_dir=ctx.data_dir,
                                                  on_progress=on_progress, on_event=on_event)
        except StreamCancelled:
            return None                     # cancelled mid-stream: nothing stored, the finalizer ends every row 'cancelled'
        except LLMError as e:
            if not ctx.cancelled.is_set():
                async with pool.connection() as c:
                    await c.execute("UPDATE analysis_run SET status='failed', error=%s, finished_at=now() WHERE id=%s",
                                    (str(e), row["id"]))
            raise StepError(str(e)) from None
        async with pool.connection() as c:
            async with c.transaction():
                await _persist(c, row["id"], kind, result)
    ctx.progress(1.0, "Done")
    return {"group_id": gid}


async def finalize(job, status, error):
    if _POOL is None:
        log.warning("analyse finalizer ran before the pool was bound")
        return
    gid = _analyse_params(job).get("group_id") or (job.get("params") or {}).get("group_id")
    if gid:
        await _set_rows(gid, status, error)
    if status in ("failed", "cancelled"):
        await _terminal_unload(job, status)


async def _set_rows(gid, status, error):
    msg = error or ("cancelled" if status == "cancelled" else "failed")
    async with _POOL.connection() as c:
        await c.execute("UPDATE analysis_run SET status=%s, error=%s, finished_at=now() "
                        "WHERE group_id=%s AND status IN ('queued', 'running')", (status, msg, gid))


async def _unload_wanted(job):
    """Guards of the terminal unload. -> a reason to skip, or None."""
    if _MEMORY is None or _SETTINGS is None:
        return "not bound"
    if not _SETTINGS.lmstudio.unload_on_terminal:
        return "lmstudio.unload_on_terminal is off"
    if _LEASE is not None and _LEASE.owner == "live":
        return "the live assistant holds the GPU"
    async with _POOL.connection() as c:
        if await (await c.execute("SELECT 1 FROM job WHERE status IN ('running', 'cancelling') AND id<>%s LIMIT 1",
                                  (job["id"],))).fetchone():
            return "another job is running"
        mine = _analyse_params(job).get("model_id") or (job.get("params") or {}).get("model_id")
        for q in await (await c.execute("SELECT params, steps FROM job WHERE status='queued' AND id<>%s",
                                        (job["id"],))).fetchall():
            for st in q["steps"]:
                if st["name"] == "lm_load" and mine and {**q["params"], **st["params"]}.get("model_id") == mine:
                    return "a queued job loads the same model"
    return None


async def _terminal_unload(job, status):
    jl = JobLogs.for_settings(_SETTINGS).get(job["id"]) if _SETTINGS is not None else None
    try:
        why = await _unload_wanted(job)
        if why:
            if jl:
                jl.append(None, "info", "memory", f"terminal unload skipped: {why}")
            return
        await _MEMORY.release()
        if jl:
            jl.append(None, "info", "memory", f"unloaded the app's model after the job {status}")
    except Exception as e:          # never changes the job status
        log.warning("terminal unload failed (job %s stays %s): %s", job["id"], status, type(e).__name__)
        if jl:
            jl.append(None, "warn", "memory", f"terminal unload failed: {e}")


def install(registry):
    registry.register("analyse")(analyse)
    registry.on_terminal("analyse")(finalize)


install(steps)
