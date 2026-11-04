"""run-insights B: GET /api/jobs/{id}/steps and /log (limit, after, done, 404, download)."""
import asyncio

from details.runner.steps import StepRegistry
from test_job_api import api, poll, post

UNKNOWN = "00000000-0000-0000-0000-000000000000"


def chatty():
    reg = StepRegistry()

    async def chat(ctx):
        for i in range(4):
            ctx.log("info", f"line {i}")
    reg.register("chat")(chat)
    reg.register("tail")(chat)
    return reg


def test_log_incremental_limit_done_and_unknown(db, db_url, mk_settings):
    async def body(c, log):
        jid = (await (await post(c, steps=["chat", "tail"])).json())["id"]
        await poll(c, jid, {"done"})
        full = await (await c.get(f"/api/jobs/{jid}/log")).json()
        ns = [x["n"] for x in full["lines"]]
        assert len(ns) >= 10 and ns == sorted(ns) and full["next"] == ns[-1] and full["done"] is True
        part = await (await c.get(f"/api/jobs/{jid}/log?after=6")).json()
        assert [x["n"] for x in part["lines"]] == [n for n in ns if n > 6] and part["next"] == ns[-1]
        lim = await (await c.get(f"/api/jobs/{jid}/log?limit=3")).json()
        assert [x["n"] for x in lim["lines"]] == ns[:3] and lim["next"] == ns[2] and lim["done"] is False
        huge = await (await c.get(f"/api/jobs/{jid}/log?limit=100000")).json()
        assert len(huge["lines"]) == len(ns)
        past = await (await c.get(f"/api/jobs/{jid}/log?after=9999")).json()
        assert past["lines"] == [] and past["next"] == 9999 and past["done"] is True
        for bad in ("after=x", "limit=0", "limit=-1", "after=-3"):
            assert (await c.get(f"/api/jobs/{jid}/log?{bad}")).status in (200, 422)
        assert (await c.get(f"/api/jobs/{UNKNOWN}/log")).status == 404
        assert (await c.get("/api/jobs/not-a-uuid/log")).status == 404
        assert (await c.get(f"/api/jobs/{UNKNOWN}/log?download=1")).status == 404
    api(db_url, mk_settings, body, reg=chatty())


def test_log_done_is_false_while_the_job_runs(db, db_url, mk_settings):
    reg = StepRegistry()

    async def slow(ctx):
        ctx.log("info", "started")
        await asyncio.sleep(1.5)
    reg.register("slow")(slow)

    async def body(c, log):
        jid = (await (await post(c, steps=["slow"])).json())["id"]
        from jobs_helpers import wait_until

        async def has_line():
            return any(x["msg"] == "started" for x in (await (await c.get(f"/api/jobs/{jid}/log")).json())["lines"])
        await wait_until(has_line, what="first line")
        r = await (await c.get(f"/api/jobs/{jid}/log")).json()
        assert r["done"] is False
        await poll(c, jid, {"done"})
    api(db_url, mk_settings, body, reg=reg)


def test_download_is_plain_text_with_filename(db, db_url, mk_settings):
    async def body(c, log):
        jid = (await (await post(c, steps=["chat"])).json())["id"]
        await poll(c, jid, {"done"})
        r = await c.get(f"/api/jobs/{jid}/log?download=1")
        assert r.status == 200 and r.headers["Content-Type"].startswith("text/plain")
        cd = r.headers["Content-Disposition"]
        assert cd.startswith("attachment") and jid[:8] in cd
        text = await r.text()
        assert "line 0" in text and "line 3" in text and not text.lstrip().startswith("{")
        assert len(text.strip().splitlines()) >= 6
    api(db_url, mk_settings, body, reg=chatty())


def test_steps_endpoint_pending_and_404(db, db_url, mk_settings):
    reg = chatty()

    async def hang(ctx):
        await asyncio.sleep(30)
    reg.register("hang")(hang)

    async def body(c, log):
        jid = (await (await post(c, steps=["chat", "hang", "tail"])).json())["id"]
        from jobs_helpers import wait_until

        async def at_hang():
            r = await (await c.get(f"/api/jobs/{jid}/steps")).json()
            return r["steps"][1]["status"] == "running"
        await wait_until(at_hang, what="hang running")
        r = await (await c.get(f"/api/jobs/{jid}/steps")).json()
        assert [s["status"] for s in r["steps"]] == ["done", "running", "pending"]
        assert r["steps"][1]["started_at"] and r["steps"][1]["duration_ms"] is None
        await c.post(f"/api/jobs/{jid}/cancel")
        await poll(c, jid, {"cancelled"})
        r = await (await c.get(f"/api/jobs/{jid}/steps")).json()
        assert [s["status"] for s in r["steps"]] == ["done", "cancelled", "pending"]
        assert (await c.get(f"/api/jobs/{UNKNOWN}/steps")).status == 404
    api(db_url, mk_settings, body, reg=reg)
