"""GET /api/models: the registry with installed flags (?kind=stt|llm|diar -> flat list; ?kind=stt_languages -> {primary, extra})."""
from aiohttp import web

from ..services import models, stt_runs


def add_models_routes(app, settings, registry, ctl):
    async def list_models(request):
        kind = request.query.get("kind")
        if kind not in (None, "stt", "llm", "diar", "stt_languages"):
            return web.json_response({"error": "kind must be stt, llm, diar or stt_languages"}, status=422)
        body = await models.describe(registry, settings, ctl)
        body["stt_languages"] = {"primary": [c for c in stt_runs.LANGUAGES if c != "auto"], "extra": settings.stt.extras()}
        return web.json_response(body[kind] if kind else body)

    app.router.add_get("/api/models", list_models)
