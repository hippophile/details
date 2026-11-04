"""Comparison API: POST/GET /api/comparisons, GET /api/comparisons/{id}[/transcripts]."""
from aiohttp import web

from ..services import comparison


def add_comparisons_routes(app, settings, pool_key, registry):
    def pool(request):
        return request.app[pool_key]

    def err(msg, status):
        return web.json_response({"error": msg}, status=status)

    async def create(request):
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return err("expected a JSON object", 422)
        try:
            cid = await comparison.create(pool(request), registry, body.get("recording_id"), body.get("a"), body.get("b"))
        except comparison.CompareError as e:
            return err(e.message, e.status)
        return web.json_response({"id": cid}, status=201)

    async def list_(request):
        out = await comparison.list_for(pool(request), request.query.get("recording_id"))
        return web.json_response(out) if out is not None else err("recording_id is required", 422)

    async def get_(request):
        out = await comparison.get(pool(request), registry, request.match_info["id"])
        return web.json_response(out) if out else err("comparison not found", 404)

    async def transcripts(request):
        out = await comparison.transcripts(pool(request), request.match_info["id"])
        return web.json_response(out) if out else err("comparison not found", 404)

    app.router.add_post("/api/comparisons", create)
    app.router.add_get("/api/comparisons", list_)
    app.router.add_get("/api/comparisons/{id}", get_)
    app.router.add_get("/api/comparisons/{id}/transcripts", transcripts)
