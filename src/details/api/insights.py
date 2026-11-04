"""Run insights: GET /api/jobs/{id}/steps, GET /api/jobs/{id}/log (?after&limit, ?download=1) and
GET /api/analysis-runs/{run_id}/inspect (the retry chain with prompts and raw replies)."""
import json

from aiohttp import web

from ..runner.joblog import JobLogs, redact, redact_exact, secrets_from
from ..services import library
from ..services.jobs import TERMINAL
from .jobs import JOBS, job_steps

MAX_LIMIT = 1000


def add_insights_routes(app, pool_key):
    def err(msg, status):
        return web.json_response({"error": msg}, status=status)

    def settings_of(request):
        from ..app import SETTINGS          # late: app.py imports this module
        return request.app[SETTINGS]

    async def job_of(request):
        svc = request.app.get(JOBS)
        if svc is None:
            raise web.HTTPServiceUnavailable(text=json.dumps({"error": "database not configured"}),
                                             content_type="application/json")
        job = await svc.get(request.match_info["id"])
        if job is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "job not found"}), content_type="application/json")
        return job

    async def steps(request):
        job = await job_of(request)
        return web.json_response({"job_id": job["id"], "steps": job_steps(request, job)})

    def number(request, name, default, lo, hi):
        try:
            return max(lo, min(hi, int(request.query.get(name, default))))
        except ValueError:
            return default

    async def log(request):
        job = await job_of(request)
        jl = JobLogs.for_settings(settings_of(request)).get(job["id"])
        if request.query.get("download"):
            return web.Response(text=jl.text(jl.read(0, 10 ** 9)), content_type="text/plain", charset="utf-8",
                                headers={"Content-Disposition": f'attachment; filename="job-{job["id"][:8]}.log"'})
        after = number(request, "after", 0, 0, 10 ** 12)
        limit = number(request, "limit", MAX_LIMIT, 1, MAX_LIMIT)
        lines = jl.read(after, limit + 1)
        more = len(lines) > limit
        lines = lines[:limit]
        return web.json_response({"lines": lines, "next": lines[-1]["n"] if lines else after,
                                  "done": job["status"] in TERMINAL and not more})

    async def inspect(request):
        rid = library.as_uuid(request.match_info["run_id"])
        if not rid:
            return err("analysis run not found", 404)
        settings = settings_of(request)
        secrets = secrets_from(settings)
        async with request.app[pool_key].connection() as c:
            row = await (await c.execute("SELECT group_id, kind FROM analysis_run WHERE id=%s", (rid,))).fetchone()
            if row is None:
                return err("analysis run not found", 404)
            rows = await (await c.execute(
                "SELECT a.id, a.retry_of, a.status, a.rendered_prompt, a.raw_response->>'text' AS raw_text, "
                "a.tokens_in, a.tokens_out, a.tok_per_s, a.wall_ms, a.finish_reason, a.schema_valid, "
                "a.validation_errors, a.error, a.params, a.model_id, a.llm_endpoint, a.output_language, "
                "t.name AS t_name, t.version AS t_version, t.sha256 AS t_sha "
                "FROM analysis_run a LEFT JOIN prompt_template t ON t.id=a.prompt_template_id "
                "WHERE a.group_id=%s AND a.kind=%s", (row["group_id"], row["kind"]))).fetchall()
        by_prev = {r["retry_of"]: r for r in rows}
        chain, cur = [], next((r for r in rows if r["retry_of"] is None), None)
        while cur is not None and cur["id"] not in [x["id"] for x in chain]:
            chain.append(cur)
            cur = by_prev.get(cur["id"])
        if not chain:
            return err("analysis run not found", 404)
        last = chain[-1]
        params = last["params"] or {}
        mapreduce = params.get("mode") == "map-reduce"
        attempts = [{"attempt": n, "run_id": str(r["id"]), "retry_of": str(r["retry_of"]) if r["retry_of"] else None,
                     "status": r["status"], "rendered_prompt": redact_exact(r["rendered_prompt"], secrets),
                     "raw_text": redact_exact(r["raw_text"], secrets), "tokens_in": r["tokens_in"],
                     "tokens_out": r["tokens_out"], "tok_per_s": r["tok_per_s"], "wall_ms": r["wall_ms"],
                     "finish_reason": r["finish_reason"], "schema_valid": r["schema_valid"],
                     "validation_errors": r["validation_errors"],
                     "error": redact(r["error"], secrets) if r["error"] else None}
                    for n, r in enumerate(chain, 1)]
        return web.json_response({
            "run_id": str(rid), "kind": row["kind"], "model_id": last["model_id"],
            "endpoint": redact(last["llm_endpoint"], secrets) if last["llm_endpoint"] else None,
            "template": {"name": last["t_name"], "version": last["t_version"], "sha256": last["t_sha"]},
            "output_language": last["output_language"],
            "params": json.loads(redact(json.dumps(params), secrets)),
            "prompt_scope": "first_chunk" if mapreduce else "full",
            "raw_text_kind": "merged_by_code" if mapreduce else "model_reply",
            "attempts": attempts})

    app.router.add_get("/api/jobs/{id}/steps", steps)
    app.router.add_get("/api/jobs/{id}/log", log)
    app.router.add_get("/api/analysis-runs/{run_id}/inspect", inspect)
