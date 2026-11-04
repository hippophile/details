"""Step `analyse`: one LM Studio call chain per output kind (validate, retry once, verify quotes, map-reduce for long
transcripts), persisted as analysis_run + output rows. Finalizer `on_terminal`: a job that ends cancelled/failed (even
while queued) sets its unfinished analysis_run rows to match. The pool is bound at startup (`bind`)."""
import hashlib, logging, types

from psycopg.types.json import Jsonb

from ..llm import client, pipeline
from ..llm.client import LLMError
from ..llm.lmstudio_ctl import identifier_for
from ..llm.prompts import language_name
from .steps import StepError, steps

log = logging.getLogger("details.analyse")
_POOL = None
RUN_COLS = ("group_id, stt_run_id, kind, prompt_template_id, llm_endpoint, model_id, params, output_language, "
            "input_text_sha256")


def bind(pool):
    global _POOL
    _POOL = pool


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
    pool, p = _POOL, ctx.params
    gid = p.get("group_id")
    async with pool.connection() as c:
        rows = await (await c.execute("SELECT a.id, a.kind, a.prompt_template_id, a.output_language, a.stt_run_id, "
                                      "t.body, t.sha256 FROM analysis_run a LEFT JOIN prompt_template t "
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

    async def chat(messages, *, kind, schema, max_tokens):
        return await client.chat(ctx.settings, model=model_name, kind=kind, schema=schema, messages=messages,
                                 max_tokens=max_tokens)

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
        template = types.SimpleNamespace(body=row["body"], sha256=row["sha256"])

        def on_progress(frac, msg=""):
            ctx.progress((n + frac) / len(rows), msg)
        try:
            result = await pipeline.generate_kind(chat, settings=ctx.settings, kind=kind, template=template,
                                                  model=model_name, context=entry.context, segments=segs,
                                                  language=language, title=run["title"], data_dir=ctx.data_dir,
                                                  on_progress=on_progress)
        except LLMError as e:
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
    if not gid:
        return
    msg = error or ("cancelled" if status == "cancelled" else "failed")
    async with _POOL.connection() as c:
        await c.execute("UPDATE analysis_run SET status=%s, error=%s, finished_at=now() "
                        "WHERE group_id=%s AND status IN ('queued', 'running')", (status, msg, gid))


def install(registry):
    registry.register("analyse")(analyse)
    registry.on_terminal("analyse")(finalize)


install(steps)
