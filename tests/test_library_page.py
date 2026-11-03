import asyncio

from conftest import client_for
from details.app import create_app


def test_library_page_served(mk_settings):
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            r = await c.get("/library")
            assert r.status == 200
            t = await r.text()
            for needle in ('id="q"', 'type="file"', "<table", 'id="trashToggle"', 'aria-live="polite"',
                           "/library/library.js", 'id="nav"', 'href="/record"'):
                assert needle in t, needle
            r = await c.get("/library/library.js")
            assert r.status == 200
            js = await r.text()
            for needle in ("/analyse?rec=", "el(\"audio\"", "/audio", "textContent", "confirm"):
                assert needle in js, needle
            assert "innerHTML" not in js  # titles must render as text only
    asyncio.run(go())
