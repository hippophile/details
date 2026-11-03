"""Local-only request guard: localhost Host header only; same-origin Origin for writes and WebSocket upgrades."""
import urllib.parse

from aiohttp import web

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}
WRITE_METHODS = {"POST", "PATCH", "DELETE"}


def _hostname(host):
    try:
        return (urllib.parse.urlsplit("//" + host).hostname or "").lower()
    except ValueError:
        return ""


@web.middleware
async def local_only(request, handler):
    if _hostname(request.headers.get("Host", "")) not in ALLOWED_HOSTS:
        raise web.HTTPForbidden(text="forbidden host")
    is_ws = request.headers.get("Upgrade", "").lower() == "websocket"
    origin = request.headers.get("Origin")
    if origin is not None and (request.method in WRITE_METHODS or is_ws):
        if urllib.parse.urlsplit(origin).netloc.lower() != request.host.lower():
            raise web.HTTPForbidden(text="forbidden origin")
    return await handler(request)
