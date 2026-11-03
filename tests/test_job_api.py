import asyncio, json

from conftest import client_for
from details.app import create_app
from details.runner.steps import StepRegistry
from fakes.engines import FakeStep
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from jobs_helpers import wait_until


def api(db_url, mk_settings, body, reg=None, **env):
    """body(client, log) runs against an app with a fake-step registry and a started runner."""
    async def go():
        log, reg_ = [], reg or StepRegistry()
        if reg is None:
            FakeStep(log, "fake", sleep=0.1).register(reg_)
        s = mk_settings(db_url, DETAILS_RUNNER_CANCEL_GRACE_S="1", **env)
        app = create_app(s, step_registry=reg_, lm_transport=FakeLMStudio(), meminfo=FakeMemInfo())
        async with client_for(app) as c:
            await body(c, log)
    asyncio.run(asyncio.wait_for(go(), 40))


async def poll(c, jid, want, timeout=10):
    box = {}

    async def check():
        box.update(await (await c.get(f"/api/jobs/{jid}")).json())
        return box["status"] in want
    await wait_until(check, timeout, f"status {want}")
    return box


async def post(c, **body):
    return await c.post("/api/jobs", json={"kind": "test", "recording_id": None, "params": {}, **body})


def test_create_and_poll(db, db_url, mk_settings):
    async def body(c, log):
        r = await post(c, steps=["fake"])
        assert r.status == 201
        j = await r.json()
        assert j["status"] == "queued" and j["steps"][0]["name"] == "fake"
        seen = {j["status"]}
        done = await poll(c, j["id"], {"done"})
        assert done["step"] == "fake" and done["step_progress"] is not None
        lst = await (await c.get("/api/jobs")).json()
        assert [x["id"] for x in lst] == [j["id"]]
    api(db_url, mk_settings, body)


def test_unknown_step_422_no_row(db, db_url, mk_settings):
    async def body(c, log):
        r = await post(c, steps=["nope"])
        assert r.status == 422 and "nope" in (await r.json())["error"]
        assert await (await c.get("/api/jobs")).json() == []
    api(db_url, mk_settings, body)


def test_bad_bodies_422(db, db_url, mk_settings):
    async def body(c, log):
        assert (await c.post("/api/jobs", data=b"not json")).status == 422
        assert (await c.post("/api/jobs", json={"kind": "t", "steps": []})).status == 422
        assert (await c.post("/api/jobs", json={"steps": ["fake"]})).status == 422
        assert (await c.post("/api/jobs", json={"kind": "t", "steps": ["fake"],
                                                "recording_id": "00000000-0000-0000-0000-000000000000"})).status == 422
    api(db_url, mk_settings, body)


def test_lm_load_unknown_model_422(db, db_url, mk_settings):
    async def body(c, log):
        r = await post(c, steps=[{"name": "lm_load", "params": {"model_id": "nope"}}])
        assert r.status == 422 and "nope" in (await r.json())["error"]
    api(db_url, mk_settings, body)


def test_404_unknown_and_malformed_ids(db, db_url, mk_settings):
    async def body(c, log):
        for path in ("/api/jobs/00000000-0000-0000-0000-000000000000", "/api/jobs/zzz",
                     "/api/jobs/00000000-0000-0000-0000-000000000000/events"):
            assert (await c.get(path)).status == 404, path
        for act in ("cancel", "retry"):
            assert (await c.post(f"/api/jobs/00000000-0000-0000-0000-000000000000/{act}")).status == 404
    api(db_url, mk_settings, body)


def test_wrong_state_409_job_unchanged(db, db_url, mk_settings):
    async def body(c, log):
        jid = (await (await post(c, steps=["fake"])).json())["id"]
        await poll(c, jid, {"done"})
        for act in ("cancel", "retry"):
            assert (await c.post(f"/api/jobs/{jid}/{act}")).status == 409
        assert (await poll(c, jid, {"done"}))["status"] == "done"
    api(db_url, mk_settings, body)


def test_cancel_and_retry_endpoints(db, db_url, mk_settings):
    async def body(c, log):
        jid = (await (await post(c, steps=["slow"])).json())["id"]
        await wait_until(lambda: log_, what="started")
        r = await c.post(f"/api/jobs/{jid}/cancel")
        assert r.status == 200 and (await r.json())["status"] == "cancelling"
        await poll(c, jid, {"cancelled"}, timeout=4)
        assert (await c.post(f"/api/jobs/{jid}/retry")).status == 409   # cancelled is not retryable
        j2 = (await (await post(c, steps=["bad"])).json())["id"]
        await poll(c, j2, {"failed"})
        r = await c.post(f"/api/jobs/{j2}/retry")
        assert r.status == 200
        await poll(c, j2, {"done"})
    log_, reg = [], StepRegistry()
    FakeStep(log_, "slow", sleep=30).register(reg)
    FakeStep(log_, "bad", fail=1).register(reg)
    api(db_url, mk_settings, body, reg=reg)


async def read_events(resp, timeout=15):
    """Collect SSE events until the server closes the stream. -> [(id, data_dict)]"""
    out, cur = [], {}

    async def loop():
        async for raw in resp.content:
            line = raw.decode().rstrip("\n")
            if line.startswith("id:"):
                cur["id"] = line[3:].strip()
            elif line.startswith("data:"):
                cur["data"] = json.loads(line[5:].strip())
            elif line == "" and "data" in cur:
                out.append((cur.get("id"), cur["data"]))
                cur.clear()
    await asyncio.wait_for(loop(), timeout)
    return out


def test_sse_first_event_is_state_then_updates_then_closes(db, db_url, mk_settings):
    async def body(c, log):
        jid = (await (await post(c, steps=["fake", "fake"])).json())["id"]
        r = await c.get(f"/api/jobs/{jid}/events")
        assert r.status == 200 and r.headers["Content-Type"].startswith("text/event-stream")
        ev = await read_events(r)
        assert ev[0][1]["job"] == jid and ev[0][1]["status"] in ("queued", "running")
        assert ev[-1][1]["status"] == "done"
        assert [int(i) for i, _ in ev] == sorted(int(i) for i, _ in ev) and len({i for i, _ in ev}) == len(ev)
        assert set(ev[0][1]) == {"seq", "job", "status", "step", "pct", "msg"}
    api(db_url, mk_settings, body)


def test_sse_late_subscriber_and_reconnect(db, db_url, mk_settings):
    async def body(c, log):
        jid = (await (await post(c, steps=["fake", "slow", "fake"])).json())["id"]

        async def at_slow():
            j = await (await c.get(f"/api/jobs/{jid}")).json()
            return j["step"] == "slow" and j["step_progress"] == 0.5
        await wait_until(at_slow, what="step 2 progress")
        r = await c.get(f"/api/jobs/{jid}/events", headers={"Last-Event-ID": "7"})
        ev = await read_events(r)
        first = ev[0][1]
        assert first["step"] == "slow" and first["pct"] == 0.5 and first["status"] == "running"
        assert ev[-1][1]["status"] == "done"
    log_, reg = [], StepRegistry()
    FakeStep(log_, "fake").register(reg)
    FakeStep(log_, "slow", sleep=1.0).register(reg)
    api(db_url, mk_settings, body, reg=reg)


def test_sse_on_finished_job_sends_one_event_and_closes(db, db_url, mk_settings):
    async def body(c, log):
        jid = (await (await post(c, steps=["fake"])).json())["id"]
        await poll(c, jid, {"done"})
        ev = await read_events(await c.get(f"/api/jobs/{jid}/events"))
        assert len(ev) == 1 and ev[0][1]["status"] == "done"
    api(db_url, mk_settings, body)


def test_jobs_api_without_database_is_503(mk_settings):
    async def go():
        async with client_for(create_app(mk_settings(), lm_transport=FakeLMStudio())) as c:
            assert (await c.get("/api/jobs")).status == 503
    asyncio.run(asyncio.wait_for(go(), 10))
