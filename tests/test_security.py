import asyncio

import pytest
from aiohttp import WSServerHandshakeError, web

from conftest import client_for
from details.app import create_app


def app_with_post(settings, ran):
    app = create_app(settings)
    async def post(request):
        ran.append(1)
        return web.json_response({"ok": True})
    app.router.add_post("/_t", post)
    return app


def run(coro):
    return asyncio.run(coro)


def test_foreign_host_403(mk_settings):
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            assert (await c.get("/api/health", headers={"Host": "evil.example"})).status == 403
            assert (await c.get("/api/health", headers={"Host": "evil.example:8765"})).status == 403
    run(go())


@pytest.mark.parametrize("host", ["localhost", "localhost:8765", "127.0.0.1:1", "[::1]:8765", "[::1]"])
def test_localhost_hosts_pass(mk_settings, host):
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            assert (await c.get("/api/health", headers={"Host": host})).status == 200
    run(go())


def test_cross_origin_post_403_and_handler_not_run(mk_settings):
    ran = []
    async def go():
        async with client_for(app_with_post(mk_settings(), ran)) as c:
            r = await c.post("/_t", headers={"Origin": "http://evil.example"})
            assert r.status == 403
    run(go())
    assert ran == []


def test_same_origin_post_passes(mk_settings):
    ran = []
    async def go():
        async with client_for(app_with_post(mk_settings(), ran)) as c:
            host = f"127.0.0.1:{c.port}"
            r = await c.post("/_t", headers={"Origin": f"http://{host}"})
            assert r.status == 200
            assert (await c.post("/_t")).status == 200  # no Origin header (curl) passes
    run(go())
    assert ran == [1, 1]


def test_cross_origin_websocket_refused(mk_settings):
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            with pytest.raises(WSServerHandshakeError) as e:
                await c.ws_connect("/api/record/ws", headers={"Origin": "http://evil.example"})
            assert e.value.status == 403
            ws = await c.ws_connect("/api/record/ws", headers={"Origin": f"http://127.0.0.1:{c.port}"})
            await ws.close()
    run(go())
