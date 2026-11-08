"""Diarization API: POST/GET /api/recordings/{id}/diarization-runs, GET/DELETE /api/diarization-runs/{id},
GET /api/diarization-runs/{id}/turns, PATCH /api/diarization-runs/{id}/speakers/{n} (rename; PATCH because
security.WRITE_METHODS has no PUT, so a PUT would skip the cross-origin guard)."""
from aiohttp import web

from ..services import diarization
from .jobs import JOBS


def add_diarization_routes(app, settings, pool_key, registry):
    def pool(request):
        return request.app[pool_key]

    def err(msg, status, **extra):
        return web.json_response({"error": msg, **extra}, status=status)

    async def body_of(request):
        try:
            body = await request.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    async def start(request):
        body = await body_of(request)
        if body is None:
            return err("expected a JSON object", 422)
        try:
            run_id, job_id = await diarization.start_run(
                pool(request), request.app[JOBS], registry, settings, request.match_info["id"], body.get("model_id"),
                body.get("min_speakers"), body.get("max_speakers"))
        except diarization.StartError as e:
            return err(e.message, e.status, **({"hint": e.hint} if e.hint else {}))
        return web.json_response({"diarization_run_id": run_id, "job_id": job_id}, status=202)

    async def list_(request):
        out = await diarization.list_runs(pool(request), registry, request.match_info["id"])
        return web.json_response(out) if out is not None else err("recording not found", 404)

    async def get_(request):
        run = await diarization.get_run(pool(request), registry, request.match_info["id"])
        return web.json_response(run) if run else err("run not found", 404)

    async def turns(request):
        rows = await diarization.get_turns(pool(request), request.match_info["id"])
        return web.json_response(rows) if rows is not None else err("run not found", 404)

    async def delete(request):
        r = await diarization.delete_run(pool(request), request.match_info["id"])
        if r is None:
            return err("run not found", 404)
        if r == "busy":
            return err("run is queued or running: cancel its job first", 409)
        return web.json_response({"ok": True})

    async def rename(request):
        body = await body_of(request)
        if body is None or "name" not in body:
            return err("expected {\"name\": text} or {\"name\": null}", 422)
        try:
            n = int(request.match_info["n"])
        except ValueError:
            return err("speaker not found", 404)
        try:
            got = await diarization.rename(pool(request), request.match_info["id"], n, body["name"])
        except diarization.NameInvalid as e:
            return err(str(e), 422)
        except diarization.NameTaken as e:
            return err(str(e), 409)
        return web.json_response(got) if got else err("run or speaker not found", 404)

    app.router.add_post("/api/recordings/{id}/diarization-runs", start)
    app.router.add_get("/api/recordings/{id}/diarization-runs", list_)
    app.router.add_get("/api/diarization-runs/{id}", get_)
    app.router.add_get("/api/diarization-runs/{id}/turns", turns)
    app.router.add_delete("/api/diarization-runs/{id}", delete)
    app.router.add_patch("/api/diarization-runs/{id}/speakers/{n}", rename)
