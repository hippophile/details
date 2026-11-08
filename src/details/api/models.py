"""GET /api/models: the registry with installed flags (?kind=stt|llm|diar -> flat list)."""
from aiohttp import web

from ..services import models


def add_models_routes(app, settings, registry, ctl):
    async def list_models(request):
        kind = request.query.get("kind")
        if kind not in (None, "stt", "llm", "diar"):
            return web.json_response({"error": "kind must be stt, llm or diar"}, status=422)
        body = await models.describe(registry, settings, ctl)
        return web.json_response(body[kind] if kind else body)

    app.router.add_get("/api/models", list_models)
