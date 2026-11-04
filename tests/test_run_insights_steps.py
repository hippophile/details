"""run-insights A: step.json lifecycle, ctx.metrics, pending/interrupted, resume, SSE steps, shared GpuLease."""
import asyncio, json

from conftest import client_for
from details.app import LEASE, RUNNER, create_app
from details.runner import stepinfo, steps_analyse
from details.runner.steps import StepRegistry
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from jobs_helpers import jobs_env, wait_status, wait_until
from test_job_api import api, poll, post, read_events


def run(coro_fn, timeout=40):
    return asyncio.run(asyncio.wait_for(coro_fn(), timeout))


def metric_reg():
    reg = StepRegistry()

    async def a(ctx):
        await asyncio.sleep(0.05)

    async def b(ctx):
        ctx.metrics(model="gemma-cheap", tokens_in=100, tokens_out=50, tok_per_s=12.5)
        await asyncio.sleep(0.05)

    async def c(ctx):
        await asyncio.sleep(0.05)
    for n, f in (("a", a), ("b", b), ("c", c)):
        reg.register(n)(f)
    return reg


def test_step_json_lifecycle_and_metrics(db, db_url, mk_settings):
    async def body():
        async with jobs_env(db_url, mk_settings, steps=metric_reg()) as e:
            jid = await e.svc.enqueue("test", None, ["a", "b", "c"], {})
            await wait_status(e.svc, jid, "done")
            return jid, e.data_dir, await e.svc.get(jid)
    jid, data, job = run(body)
    b = json.loads((data / "jobs" / jid / "b" / "step.json").read_text())
    assert b["name"] == "b" and b["index"] == 1 and b["status"] == "done"
    assert b["model"] == "gemma-cheap" and b["tokens_in"] == 100 and b["tokens_out"] == 50 and b["tok_per_s"] == 12.5
    assert b["started_at"] and b["finished_at"] and b["duration_ms"] >= 40
    assert b["ram_free_start_gb"] == 64.0 and b["ram_free_end_gb"] == 64.0     # FakeMemInfo
    assert b["error"] is None
    assert not list((data / "jobs" / jid / "b").glob("*.tmp"))
    view = stepinfo.view(data, job)
    assert [s["status"] for s in view] == ["done"] * 3 and view[0]["model"] is None


def test_failed_step_record_has_error_and_later_steps_pending(db, db_url, mk_settings):
    reg = metric_reg()

    async def bad(ctx):
        raise RuntimeError("kaput")
    reg.register("b")(bad)

    async def body():
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("test", None, ["a", "b", "c"], {})
            job = await wait_status(e.svc, jid, "failed")
            return stepinfo.view(e.data_dir, job)
    view = run(body)
    assert [s["status"] for s in view] == ["done", "failed", "pending"]
    assert "kaput" in view[1]["error"] and view[2]["started_at"] is None and view[2]["name"] == "c"


def test_running_record_on_a_queued_job_is_interrupted(tmp_path):
    d = tmp_path / "jobs" / "jid" / "a"
    d.mkdir(parents=True)
    stepinfo.write(d, {"index": 0, "name": "a", "status": "running", "started_at": "2026-11-04T10:00:00+00:00"})
    job = {"id": "jid", "status": "queued", "step_index": 0, "steps": [{"name": "a"}, {"name": "b"}]}
    assert [s["status"] for s in stepinfo.view(tmp_path, job)] == ["interrupted", "pending"]
    job["status"] = "running"
    assert stepinfo.view(tmp_path, job)[0]["status"] == "running"


def test_resumed_job_keeps_finished_step_records(db, db_url, mk_settings):
    reg, seen = metric_reg(), {}

    async def flaky(ctx):
        seen["n"] = seen.get("n", 0) + 1
        if seen["n"] == 1:
            raise RuntimeError("first time")
    reg.register("b")(flaky)

    async def body():
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("test", None, ["a", "b", "c"], {})
            await wait_status(e.svc, jid, "failed")
            first = json.loads((e.data_dir / "jobs" / jid / "a" / "step.json").read_text())
            await e.svc.retry(jid)
            job = await wait_status(e.svc, jid, "done")
            return first, stepinfo.view(e.data_dir, job)
    first, view = run(body)
    assert [s["status"] for s in view] == ["done", "done", "done"]
    assert view[0]["duration_ms"] == first["duration_ms"] and view[0]["started_at"] == first["started_at"]


def test_step_waits_for_gpu_lease_and_started_at_is_set_on_grant(db, db_url, mk_settings):
    async def body():
        async with jobs_env(db_url, mk_settings, steps=metric_reg()) as e:
            hold = e.runner.lease.acquire("live")
            await hold.wait()
            jid = await e.svc.enqueue("test", None, ["a"], {})

            def waiting():
                try:
                    r = json.loads((e.data_dir / "jobs" / jid / "a" / "step.json").read_text())
                except OSError:
                    return None
                return r if r["status"] == "waiting_gpu" else None
            rec = await wait_until(waiting, what="waiting_gpu")
            assert rec["started_at"] is None and rec["ram_free_start_gb"] is None
            hold.release()
            job = await wait_status(e.svc, jid, "done")
            return stepinfo.view(e.data_dir, job)[0]
    done = run(body)
    assert done["status"] == "done" and done["started_at"] is not None


def test_sse_state_gains_steps_and_keeps_old_fields(db, db_url, mk_settings):
    async def body(c, log):
        jid = (await (await post(c, steps=["a", "b", "c"])).json())["id"]
        ev = await read_events(await c.get(f"/api/jobs/{jid}/events"))
        assert set(ev[0][1]) == {"seq", "job", "status", "step", "pct", "msg", "steps"}
        last = ev[-1][1]
        assert last["status"] == "done" and [s["status"] for s in last["steps"]] == ["done"] * 3
        assert last["steps"][1]["model"] == "gemma-cheap" and last["steps"][1]["tokens_in"] == 100
        assert [s["name"] for s in ev[0][1]["steps"]] == ["a", "b", "c"]
        r = await (await c.get(f"/api/jobs/{jid}/steps")).json()
        assert r["job_id"] == jid and r["steps"] == last["steps"]
        assert (await c.get("/api/jobs/00000000-0000-0000-0000-000000000000/steps")).status == 404
    api(db_url, mk_settings, body, reg=metric_reg())


def test_sse_emits_a_new_event_when_metrics_change(db, db_url, mk_settings):
    reg = StepRegistry()

    async def slow(ctx):
        await asyncio.sleep(0.6)
        ctx.metrics(model="m", tokens_in=1, tokens_out=2)
        await asyncio.sleep(1.2)
    reg.register("slow")(slow)

    async def body(c, log):
        jid = (await (await post(c, steps=["slow"])).json())["id"]
        ev = await read_events(await c.get(f"/api/jobs/{jid}/events"))
        withm = [d for _, d in ev if d["steps"][0]["tokens_in"] == 1]
        assert withm and withm[0]["status"] == "running" and withm[0]["step"] == "slow"
    api(db_url, mk_settings, body, reg=reg)


def test_step_names_starting_with_underscore_are_rejected():
    import pytest
    with pytest.raises(ValueError):
        StepRegistry().register("_job")(lambda ctx: None)


def test_create_app_passes_one_app_stored_gpulease_to_the_runner(db, db_url, mk_settings):
    async def go():
        app = create_app(mk_settings(db_url), step_registry=StepRegistry(), lm_transport=FakeLMStudio(),
                         meminfo=FakeMemInfo())
        async with client_for(app):
            assert app[LEASE] is app[RUNNER].lease
    asyncio.run(asyncio.wait_for(go(), 20))


def test_bind_with_only_a_pool_still_works():
    steps_analyse.bind(None)
    steps_analyse.bind(None, memory=None, settings=None, hub=None)
