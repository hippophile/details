"""llm-streaming: GET /api/jobs/{id}/stream (SSE) and the Analyse page assets for the live box."""
import asyncio, json, re, shutil, subprocess, sys

import pytest

from conftest import client_for
from details.api.jobs import STREAM_HUB
from details.app import create_app
from details.runner.steps import StepRegistry
from details.settings import REPO_ROOT
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from test_job_api import poll, post


def hold_registry():
    reg = StepRegistry()
    release = {}

    async def hold(ctx):
        ev = release.setdefault(ctx.job_id, asyncio.Event())
        w = asyncio.create_task(ev.wait())
        c = asyncio.create_task(ctx.cancelled.wait())
        await asyncio.wait({w, c}, return_when=asyncio.FIRST_COMPLETED)
        w.cancel()
        c.cancel()
    reg.register("hold")(hold)
    return reg, release


def api(db_url, mk_settings, body, **env):
    async def go():
        reg, release = hold_registry()
        s = mk_settings(db_url, DETAILS_RUNNER_CANCEL_GRACE_S="1", **env)
        app = create_app(s, step_registry=reg, lm_transport=FakeLMStudio(), meminfo=FakeMemInfo())
        async with client_for(app) as c:
            await body(c, app[STREAM_HUB], release)
    asyncio.run(asyncio.wait_for(go(), 40))


async def running_job(c):
    j = await (await post(c, steps=["hold"])).json()
    await poll(c, j["id"], {"running"})
    return j["id"]


async def read_event(r, timeout=5):
    """-> {"event", "id", "data"} of the next SSE event, skipping comments; None at EOF."""
    ev = {}
    while True:
        line = await asyncio.wait_for(r.content.readline(), timeout)
        if not line:
            return None
        line = line.decode().rstrip("\n")
        if line == "":
            if ev:
                return ev
        elif line.startswith(":"):
            continue
        else:
            k, _, v = line.partition(": ")
            ev[k] = json.loads(v) if k == "data" else v


def test_unknown_job_is_404(db, db_url, mk_settings):
    async def body(c, hub, release):
        assert (await c.get("/api/jobs/00000000-0000-0000-0000-000000000001/stream")).status == 404
        assert (await c.get("/api/jobs/not-a-uuid/stream")).status == 404
    api(db_url, mk_settings, body)


def test_late_subscriber_gets_a_snapshot_then_the_rest(db, db_url, mk_settings):
    async def body(c, hub, release):
        jid = await running_job(c)
        hub.start_attempt(jid, "checklist", 1)
        hub.delta(jid, "x" * 200)
        r = await c.get(f"/api/jobs/{jid}/stream")
        assert r.status == 200 and r.headers["Content-Type"].startswith("text/event-stream")
        first = await read_event(r)
        assert first["event"] == "snapshot" and first["data"]["text"] == "x" * 200
        assert first["data"]["kind"] == "checklist" and first["data"]["attempt"] == 1 and first["id"]
        hub.delta(jid, "tail")
        d = await read_event(r)
        assert d["event"] == "delta" and d["data"]["text"] == "tail" and int(d["id"]) > int(first["id"])
        release.setdefault(jid, asyncio.Event()).set()
    api(db_url, mk_settings, body)


def test_two_subscribers_see_the_same_text(db, db_url, mk_settings):
    async def body(c, hub, release):
        jid = await running_job(c)
        hub.start_attempt(jid, "tags", 1)
        r1, r2 = await c.get(f"/api/jobs/{jid}/stream"), await c.get(f"/api/jobs/{jid}/stream")
        assert (await read_event(r1))["event"] == (await read_event(r2))["event"] == "snapshot"
        hub.delta(jid, "hello ")
        hub.delta(jid, "world")
        got = []
        for r in (r1, r2):
            text = ""
            while text != "hello world":
                e = await read_event(r)
                assert e["event"] == "delta"
                text += e["data"]["text"]
            got.append(text)
        assert got[0] == got[1]
        release.setdefault(jid, asyncio.Event()).set()
    api(db_url, mk_settings, body)


def test_retry_sends_attempt_start_and_new_text_starts_empty(db, db_url, mk_settings):
    async def body(c, hub, release):
        jid = await running_job(c)
        hub.start_attempt(jid, "tags", 1)
        r = await c.get(f"/api/jobs/{jid}/stream")
        await read_event(r)
        hub.delta(jid, "bad")
        assert (await read_event(r))["event"] == "delta"
        hub.end_attempt(jid, valid=False, finish_reason="stop", tokens_out=1)
        e = await read_event(r)
        assert e["event"] == "attempt_end" and e["data"]["valid"] is False
        hub.start_attempt(jid, "tags", 2)
        hub.delta(jid, "good")
        e = await read_event(r)
        assert e["event"] == "attempt_start" and e["data"]["attempt"] == 2 and e["data"]["kind"] == "tags"
        assert (await read_event(r))["data"]["text"] == "good"
        hub.fallback(jid, "http 400")
        e = await read_event(r)
        assert e["event"] == "fallback" and "400" in e["data"]["reason"]
        release.setdefault(jid, asyncio.Event()).set()
    api(db_url, mk_settings, body)


def test_output_is_coalesced_to_at_most_one_write_per_100ms(db, db_url, mk_settings):
    async def body(c, hub, release):
        jid = await running_job(c)
        hub.start_attempt(jid, "tags", 1)
        r = await c.get(f"/api/jobs/{jid}/stream")
        await read_event(r)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for i in range(60):
            hub.delta(jid, f"{i};")
            await asyncio.sleep(0.005)                          # 60 deltas in about 0.3-0.4 s
        sent = loop.time() - t0
        events, text, pieces = [], "", 0
        while pieces < 60:
            e = await read_event(r)
            events.append(e)
            text += e["data"]["text"]
            pieces += e["data"]["n"]
        assert text == "".join(f"{i};" for i in range(60))
        assert len(events) <= sent / 0.1 + 2 and len(events) < 15
        release.setdefault(jid, asyncio.Event()).set()
    api(db_url, mk_settings, body)


def test_last_event_id_resumes_from_the_buffer_else_snapshot(db, db_url, mk_settings):
    async def body(c, hub, release):
        jid = await running_job(c)
        hub.start_attempt(jid, "tags", 1)
        hub.delta(jid, "one")
        mid = hub.snapshot(jid)["seq"]
        hub.delta(jid, "two")
        r = await c.get(f"/api/jobs/{jid}/stream", headers={"Last-Event-ID": str(mid)})
        e = await read_event(r)
        assert e["event"] == "delta" and e["data"]["text"] == "two"
        r.close()
        hub.start_attempt(jid, "tags", 2)
        for i in range(5000):                                  # mid fell out of the event ring
            hub.delta(jid, "z")
        r = await c.get(f"/api/jobs/{jid}/stream", headers={"Last-Event-ID": str(mid)})
        e = await read_event(r)
        assert e["event"] == "snapshot" and e["data"]["attempt"] == 2 and e["data"]["text"].startswith("z")
        release.setdefault(jid, asyncio.Event()).set()
    api(db_url, mk_settings, body)


def test_job_that_is_not_streaming_gets_an_empty_snapshot_and_a_close_at_the_end(db, db_url, mk_settings):
    async def body(c, hub, release):
        jid = await running_job(c)
        r = await c.get(f"/api/jobs/{jid}/stream")
        e = await read_event(r)
        assert e["event"] == "snapshot" and e["data"]["text"] == "" and e["data"]["active"] is False
        release.setdefault(jid, asyncio.Event()).set()
        end = await read_event(r, 10)
        while end is not None and end["event"] != "end":
            end = await read_event(r, 10)
        assert end["data"]["status"] == "done" and await read_event(r) is None
    api(db_url, mk_settings, body)


def test_finished_job_is_a_snapshot_and_a_closed_stream(db, db_url, mk_settings):
    async def body(c, hub, release):
        jid = await running_job(c)
        release.setdefault(jid, asyncio.Event()).set()
        await poll(c, jid, {"done"})
        r = await c.get(f"/api/jobs/{jid}/stream")
        assert r.status == 200
        e = await read_event(r)
        assert e["event"] == "snapshot" and e["data"]["text"] == ""
        rest = []
        while (x := await read_event(r)) is not None:
            rest.append(x["event"])
        assert rest == ["end"]
    api(db_url, mk_settings, body)


def test_stream_closes_when_the_job_is_cancelled(db, db_url, mk_settings):
    async def body(c, hub, release):
        jid = await running_job(c)
        r = await c.get(f"/api/jobs/{jid}/stream")
        await read_event(r)
        await c.post(f"/api/jobs/{jid}/cancel")
        last = None
        while (x := await read_event(r, 10)) is not None:
            last = x
        assert last["event"] == "end" and last["data"]["status"] == "cancelled"
    api(db_url, mk_settings, body)


# ---- the page -----------------------------------------------------------------------------------------------

def page_text(mk_settings, *paths):
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            out = []
            for p in paths:
                r = await c.get(p)
                assert r.status == 200, p
                out.append(await r.text())
            return out
    return asyncio.run(go())


def test_page_has_the_live_box_ids_and_loads_stream_assets_in_order(mk_settings):
    html, = page_text(mk_settings, "/analyse")
    for i in ("liveBox", "liveHead", "liveText", "liveNote"):
        assert f'id="{i}"' in html, i
    assert "draft, not yet checked" in html and "The model is writing" in html
    assert "/analyse/stream.css" in html
    assert html.index("/analyse/api.js") < html.index("/analyse/stream.js") < html.index("/analyse/analyse.js")
    for old in ("progress", "stage", "pct", "elapsed", "cancel", "barfill", "stepTable", "logPanel"):
        assert f'id="{old}"' in html, old


def test_stream_js_is_textcontent_only_and_listens_to_the_stream(mk_settings):
    js, css = page_text(mk_settings, "/analyse/stream.js", "/analyse/stream.css")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js
    for needle in ("textContent", "/stream", "EventSource", "snapshot", "live view unavailable", "scroll"):
        assert needle in js, needle
    assert not re.search(r"gemma-(cheap|nice)|mlx-community", js) and "liveText" in css


def test_node_stream_unit_tests():
    node = shutil.which("node")
    if not node:
        sys.__stderr__.write("\nSKIP: node not found (stream JS helper tests not run)\n")
        pytest.skip("node not found")
    r = subprocess.run([node, "--test", str(REPO_ROOT / "tests" / "js" / "stream.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]
