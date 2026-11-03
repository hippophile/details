"""Library API: /api/recordings (list, get, rename, trash, restore, audio, import)."""
from aiohttp import web

from ..services import library


def add_recordings_routes(app, settings, pool_key):
    def pool(request):
        return request.app[pool_key]

    def not_found():
        return web.json_response({"error": "recording not found"}, status=404)

    async def list_(request):
        q = request.query.get("q", "").strip()
        return web.json_response(await library.list_items(pool(request), q, request.query.get("trash") == "1"))

    async def get_(request):
        it = await library.get_item(pool(request), request.match_info["id"])
        return web.json_response(it) if it else not_found()

    async def patch(request):
        try:
            body = await request.json()
        except ValueError:
            body = None
        title = library.clean_title(body.get("title") if isinstance(body, dict) else None)
        if title is None:
            return web.json_response({"error": f"title must be 1 to {library.MAX_TITLE} characters"}, status=422)
        it = await library.rename(pool(request), request.match_info["id"], title)
        return web.json_response(it) if it else not_found()

    async def delete(request):
        it = await library.trash(pool(request), request.match_info["id"])
        return web.json_response(it) if it else not_found()

    async def restore(request):
        it = await library.restore(pool(request), request.match_info["id"])
        return web.json_response(it) if it else not_found()

    async def audio(request):
        try:
            found = await library.audio_file(pool(request), settings, request.match_info["id"])
        except PermissionError:
            raise web.HTTPForbidden(text="audio path refused")
        if not found:
            return not_found()
        path, ctype = found
        resp = web.FileResponse(path)   # handles Range: 206 / 416, Accept-Ranges
        resp.content_type = ctype
        return resp

    async def import_(request):
        if not request.content_type.startswith("multipart/"):
            return web.json_response({"error": "expected a multipart/form-data upload"}, status=422)
        try:
            it = await library.import_upload(pool(request), settings, await request.multipart(), request.content_length)
        except library.ImportError_ as e:
            return web.json_response({"error": e.message}, status=e.status)
        return web.json_response(it, status=201)

    app.router.add_get("/api/recordings", list_)
    app.router.add_post("/api/recordings/import", import_)
    app.router.add_get("/api/recordings/{id}", get_)
    app.router.add_patch("/api/recordings/{id}", patch)
    app.router.add_delete("/api/recordings/{id}", delete)
    app.router.add_post("/api/recordings/{id}/restore", restore)
    app.router.add_get("/api/recordings/{id}/audio", audio)
