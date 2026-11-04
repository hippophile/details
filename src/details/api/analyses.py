"""Analysis API: POST/GET /api/stt-runs/{id}/analyses, GET /api/analyses/{group_id}, PATCH /api/outputs/{id}/items/{idx},
Markdown for one output or a whole analysis. One group_id = one Start click."""
import datetime, uuid

from aiohttp import web
from psycopg.types.json import Jsonb

from ..llm import markdown
from ..llm.prompts import latest_templates
from ..llm.schemas import KINDS
from ..services import library
from ..services.jobs import JobError
from ..services.stt_runs import fmt_wall
from .jobs import JOBS

LANGS = ("transcript", "en")
ACTIVE = ("queued", "running")
RUN_COLS = ("a.id, a.group_id, a.stt_run_id, a.kind, a.model_id, a.output_language, a.status, a.error, a.wall_ms, "
            "a.tokens_in, a.tokens_out, a.tok_per_s, a.finish_reason, a.schema_valid, a.validation_errors, "
            "a.retry_of, a.params, a.created_at, a.started_at, a.finished_at, "
            "a.raw_response->>'text' AS raw_text, o.id AS output_id, o.schema_version, o.content")


class StartError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


async def start_analysis(pool, jobs, registry, settings, stt_run_id, model_id, kinds, output_language="transcript"):
    """Insert one queued analysis_run per kind + enqueue lm_load -> analyse -> lm_unload. -> (group_id, job_id)."""
    entry = registry.llm.get(model_id) if isinstance(model_id, str) else None
    if entry is None:
        raise StartError(422, f"'{model_id}' is not an LLM model" if isinstance(model_id, str) and model_id in registry.stt
                         else f"unknown model_id '{model_id}'")
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) and k in KINDS for k in kinds):
        raise StartError(422, f"kinds must be a non-empty list of: {', '.join(KINDS)}")
    kinds = list(dict.fromkeys(kinds))
    if output_language not in LANGS:
        raise StartError(422, "output_language must be 'transcript' or 'en'")
    rid = library.as_uuid(stt_run_id)
    async with pool.connection() as c:
        run = await (await c.execute("SELECT recording_id, status FROM stt_run WHERE id=%s", (rid,))).fetchone() if rid else None
    if run is None:
        raise StartError(404, "transcript run not found")
    if run["status"] != "done":
        raise StartError(422, f"the transcript run is {run['status']}: wait until it is done")
    templates = await latest_templates(pool)
    missing = [k for k in kinds if k not in templates]
    if missing:
        raise StartError(422, f"no prompt template for {', '.join(missing)} (run `make prompts`)")
    group_id = str(uuid.uuid4())
    async with pool.connection() as c:
        async with c.transaction():
            for k in kinds:
                params = {"temperature": 0.1, "max_tokens": settings.llm.max_tokens(k), "model": entry.model,
                          "context": entry.context}
                await c.execute("INSERT INTO analysis_run (group_id, stt_run_id, kind, prompt_template_id, llm_endpoint, "
                                "model_id, params, output_language, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())",
                                (group_id, rid, k, templates[k].id, settings.lmstudio.url, model_id, Jsonb(params),
                                 output_language))
    try:
        job_id = await jobs.enqueue("analyse", str(run["recording_id"]), [
            {"name": "lm_load", "params": {"model_id": model_id}},
            {"name": "analyse", "params": {"group_id": group_id, "stt_run_id": str(rid), "model_id": model_id,
                                          "kinds": kinds, "output_language": output_language}},
            "lm_unload"])
    except JobError as e:
        async with pool.connection() as c:
            await c.execute("DELETE FROM analysis_run WHERE group_id=%s", (group_id,))
        raise StartError(422, str(e)) from None
    except BaseException:
        async with pool.connection() as c:
            await c.execute("DELETE FROM analysis_run WHERE group_id=%s", (group_id,))
        raise
    async with pool.connection() as c:
        await c.execute("UPDATE analysis_run SET params = params || %s WHERE group_id=%s",
                        (Jsonb({"job_id": job_id}), group_id))
    return group_id, job_id


def _plain(v):
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime.datetime):
        return v.isoformat()
    return v


def _group_status(statuses):
    for s in ("running", "queued", "failed", "cancelled"):
        if s in statuses:
            return s
    return "done"


def _assemble(rows, registry):
    """rows of ONE stt_run -> groups (oldest first), each with its kinds (last link of each retry chain)."""
    groups = {}
    for r in rows:
        groups.setdefault(r["group_id"], []).append(r)
    out = []
    for gid, rs in sorted(groups.items(), key=lambda kv: min(r["created_at"] for r in kv[1])):
        by_kind, order = {}, []
        for r in rs:
            if r["kind"] not in by_kind:
                order.append(r["kind"])
            by_kind.setdefault(r["kind"], []).append(r)
        kinds = []
        for k in order:
            chain = by_kind[k]
            retried = {r["retry_of"] for r in chain if r["retry_of"]}
            last = next(r for r in reversed(chain) if r["id"] not in retried)
            wall = sum(r["wall_ms"] or 0 for r in chain)
            tin, tout = sum(r["tokens_in"] or 0 for r in chain), sum(r["tokens_out"] or 0 for r in chain)
            p = last["params"] or {}
            kinds.append({"kind": k, "status": last["status"], "run_id": str(last["id"]), "error": last["error"],
                          "schema_valid": last["schema_valid"], "validation_errors": last["validation_errors"],
                          "attempts": len(chain), "wall_ms": wall, "tokens_in": tin, "tokens_out": tout,
                          "tok_per_s": round(tout / (wall / 1000), 1) if wall and tout else None,
                          "finish_reason": last["finish_reason"], "mode": p.get("mode"), "chunks": p.get("chunks"),
                          "verified": p.get("verified", 0), "unverified": p.get("unverified", 0),
                          "raw_text": last["raw_text"] if last["schema_valid"] is False else None,
                          "output": {"id": str(last["output_id"]), "schema_version": last["schema_version"],
                                     "content": last["content"]} if last["output_id"] else None})
        first = rs[0]
        wall = sum(k["wall_ms"] for k in kinds)
        tout = sum(k["tokens_out"] for k in kinds)
        m = registry.llm.get(first["model_id"])
        out.append({"group_id": str(gid), "stt_run_id": str(first["stt_run_id"]), "model_id": first["model_id"],
                    "model_label": m.label if m else first["model_id"], "output_language": first["output_language"],
                    "status": _group_status([k["status"] for k in kinds]),
                    "job_id": next((r["params"].get("job_id") for r in rs if (r["params"] or {}).get("job_id")), None),
                    "created_at": min(r["created_at"] for r in rs).isoformat(), "wall_ms": wall,
                    "tokens_in": sum(k["tokens_in"] for k in kinds), "tokens_out": tout,
                    "tok_per_s": round(tout / (wall / 1000), 1) if wall and tout else None,
                    "verified": sum(k["verified"] for k in kinds), "unverified": sum(k["unverified"] for k in kinds),
                    "error": next((k["error"] for k in kinds if k["error"] and k["status"] in ("failed", "cancelled")), None),
                    "kinds": kinds})
    for n, g in enumerate(out, 1):
        g["n"] = n
        tail = fmt_wall(g["wall_ms"]) if g["status"] == "done" and g["wall_ms"] else g["status"]
        g["label"] = f"Analysis #{n} · {g['model_label']} · {tail}"
    return out


def add_analyses_routes(app, settings, pool_key, registry):
    def pool(request):
        return request.app[pool_key]

    def err(msg, status):
        return web.json_response({"error": msg}, status=status)

    async def load_for_run(request, stt_run_id):
        async with pool(request).connection() as c:
            cur = await c.execute(f"SELECT {RUN_COLS} FROM analysis_run a LEFT JOIN output o ON o.analysis_run_id=a.id "
                                  "WHERE a.stt_run_id=%s ORDER BY a.created_at, a.id", (stt_run_id,))
            return _assemble(await cur.fetchall(), registry)

    async def start(request):
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return err("expected a JSON object", 422)
        try:
            gid, jid = await start_analysis(pool(request), request.app[JOBS], registry, settings,
                                            request.match_info["id"], body.get("model_id"), body.get("kinds"),
                                            body.get("output_language", "transcript"))
        except StartError as e:
            return err(e.message, e.status)
        return web.json_response({"group_id": gid, "job_id": jid}, status=202)

    async def list_(request):
        rid = library.as_uuid(request.match_info["id"])
        if not rid:
            return err("transcript run not found", 404)
        groups = await load_for_run(request, rid)
        for g in groups:
            g.pop("kinds")
        return web.json_response(groups[::-1])

    async def group_of(request):
        gid = library.as_uuid(request.match_info["group_id"])
        if not gid:
            return None
        async with pool(request).connection() as c:
            row = await (await c.execute("SELECT stt_run_id FROM analysis_run WHERE group_id=%s LIMIT 1", (gid,))).fetchone()
        if row is None:
            return None
        return next((g for g in await load_for_run(request, row["stt_run_id"]) if g["group_id"] == gid), None)

    async def get_(request):
        g = await group_of(request)
        return web.json_response(g) if g else err("analysis not found", 404)

    async def patch_item(request):
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("done"), bool):
            return err("expected {\"done\": true|false}", 422)
        oid = library.as_uuid(request.match_info["id"])
        async with pool(request).connection() as c:
            row = await (await c.execute("SELECT kind FROM output WHERE id=%s", (oid,))).fetchone() if oid else None
            if row is None:
                return err("output not found", 404)
            if row["kind"] != "checklist":
                return err("only checklist items can be ticked", 409)
            try:
                idx = int(request.match_info["idx"])
            except ValueError:
                idx = -1
            upd = await (await c.execute(
                "UPDATE output SET content = jsonb_set(content, ARRAY['items', %s::text, 'done'], to_jsonb(%s::boolean)) "
                "WHERE id=%s AND %s >= 0 AND jsonb_array_length(content->'items') > %s "
                "RETURNING content->'items'->%s AS item", (idx, body["done"], oid, idx, idx, idx))).fetchone()
        if upd is None:
            return err("item not found", 404)
        return web.json_response({"ok": True, "item": upd["item"]})

    def md_response(request, text, filename):
        headers = {"Content-Type": "text/markdown; charset=utf-8"}
        if request.query.get("download"):
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return web.Response(body=text.encode("utf-8"), headers=headers)

    async def output_md(request):
        oid = library.as_uuid(request.match_info["id"])
        async with pool(request).connection() as c:
            row = await (await c.execute("SELECT o.kind, o.content, a.group_id FROM output o JOIN analysis_run a "
                                         "ON a.id=o.analysis_run_id WHERE o.id=%s", (oid,))).fetchone() if oid else None
        if row is None:
            return err("output not found", 404)
        return md_response(request, markdown.render_output(row["kind"], row["content"]),
                           f"{row['kind']}-{str(row['group_id'])[:8]}.md")

    async def group_md(request):
        g = await group_of(request)
        outs = [(k["kind"], k["output"]["content"]) for k in (g["kinds"] if g else []) if k["output"]]
        if not outs:
            return err("analysis not found" if g is None else "this analysis has no outputs yet", 404)
        return md_response(request, markdown.render_group(outs), f"analysis-{g['group_id'][:8]}.md")

    app.router.add_post("/api/stt-runs/{id}/analyses", start)
    app.router.add_get("/api/stt-runs/{id}/analyses", list_)
    app.router.add_get("/api/analyses/{group_id}", get_)
    app.router.add_get("/api/analyses/{group_id}/markdown", group_md)
    app.router.add_patch("/api/outputs/{id}/items/{idx}", patch_item)
    app.router.add_get("/api/outputs/{id}/markdown", output_md)
