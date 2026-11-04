"""STT run API: POST/GET /api/recordings/{id}/stt-runs, GET /api/stt-runs/{id}[/segments], DELETE /api/stt-runs/{id}."""
from aiohttp import web

from ..services import stt_runs
from .jobs import JOBS


def add_stt_runs_routes(app, settings, pool_key, registry):
    def pool(request):
        return request.app[pool_key]

    def err(msg, status):
        return web.json_response({"error": msg}, status=status)

    async def start(request):
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return err("expected a JSON object", 422)
        if not isinstance(body.get("vocab_hint", False), bool) or \
                not isinstance(body.get("initial_prompt") or "", str):
            return err("vocab_hint must be a boolean and initial_prompt a string", 422)
        language = body.get("language", "el")
        try:
            await stt_runs.check_model(registry, settings, body.get("model_id"), language)
            run_id, job_id = await stt_runs.start_run(
                pool(request), request.app[JOBS], registry, settings, request.match_info["id"], body["model_id"],
                language=language, vocab_hint=body.get("vocab_hint", False), initial_prompt=body.get("initial_prompt"))
        except stt_runs.StartError as e:
            return err(e.message, e.status)
        return web.json_response({"stt_run_id": run_id, "job_id": job_id}, status=202)

    async def list_(request):
        out = await stt_runs.list_runs(pool(request), settings, registry, request.match_info["id"])
        return web.json_response(out) if out is not None else err("recording not found", 404)

    async def get_(request):
        run = await stt_runs.get_run(pool(request), registry, request.match_info["id"])
        return web.json_response(run) if run else err("run not found", 404)

    async def segments(request):
        segs = await stt_runs.segments(pool(request), request.match_info["id"])
        return web.json_response(segs) if segs is not None else err("run not found", 404)

    async def delete(request):
        r = await stt_runs.delete_run(pool(request), request.match_info["id"])
        if r is None:
            return err("run not found", 404)
        if r == "busy":
            return err("run is queued or running: cancel its job first", 409)
        return web.json_response({"ok": True})

    app.router.add_post("/api/recordings/{id}/stt-runs", start)
    app.router.add_get("/api/recordings/{id}/stt-runs", list_)
    app.router.add_get("/api/stt-runs/{id}", get_)
    app.router.add_get("/api/stt-runs/{id}/segments", segments)
    app.router.add_delete("/api/stt-runs/{id}", delete)
