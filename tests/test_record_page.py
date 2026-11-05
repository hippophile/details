import asyncio

from conftest import client_for
from details.app import create_app


def test_record_page_has_controls(mk_settings):
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            r = await c.get("/record")
            assert r.status == 200
            t = await r.text()
            for needle in ('id="title"', 'id="timer"', 'id="mic"', 'role="tablist"', 'href="/live"',
                           "/record/record.js", 'id="pause"', 'id="end"', "/record/pause.js"):
                assert needle in t, needle
            assert (await c.get("/record/worklet.js")).status == 200
            assert (await c.get("/record/pause.js")).status == 200
            assert "Mic on" not in t
            for f in ("record.js", "worklet.js", "pause.js"):
                assert "resampl" not in (await (await c.get("/record/" + f)).text()).lower()
    asyncio.run(go())


def test_scripts_api_ordered(db, db_url, mk_settings):
    async def go():
        async with client_for(create_app(mk_settings(db_url))) as c:
            r = await c.get("/api/scripts")
            assert r.status == 200
            data = await r.json()
            assert len(data) == 4 and all({"id", "name", "body_md"} <= set(x) for x in data)
            assert data[1]["name"].startswith("Bank BPMN review")
    asyncio.run(go())


def test_record_page_has_silence_line(mk_settings):
    """User asked for the v1 'silence line': a draggable threshold on the level meter."""
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            t = await (await c.get("/record")).text()
            for needle in ('id="threshold"', 'type="range"', 'id="thr"', 'id="thrVal"'):
                assert needle in t, needle
            js = await (await c.get("/record/record.js")).text()
            assert "silence" in js and "threshold" in js
    asyncio.run(go())
