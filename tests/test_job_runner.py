import asyncio, json, os

import pytest
from psycopg.errors import CheckViolation

from conftest import make_settings
from details.runner.steps import StepRegistry
from details.services.jobs import JobError, NotFound, WrongState
from fakes.engines import FakeStep, child_argv
from jobs_helpers import jobs_env, run, wait_status, wait_until


def job_dir(e, jid, step):
    return e.data_dir / "jobs" / str(jid) / step


# ---- migration 0003 -----------------------------------------------------------------------------------

def test_job_table_shape(db):
    cols = {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='job'")}
    assert {"id", "recording_id", "kind", "params", "steps", "status", "step", "step_index", "step_progress",
            "message", "error", "attempt", "created_at", "started_at", "finished_at", "heartbeat_at"} <= cols
    idx = [r[0] for r in db.execute("SELECT indexdef FROM pg_indexes WHERE tablename='job'")]
    assert any("(status, created_at)" in i for i in idx)


def test_job_status_check_constraint(db):
    with pytest.raises(CheckViolation):
        db.execute("INSERT INTO job (kind, status) VALUES ('x', 'bogus')")


def test_job_row_cascades_with_recording(db):
    rid = db.execute("INSERT INTO recording (title, started_at, duration_ms, audio_path, container, codec, "
                     "sample_rate, channels) VALUES ('t', now(), 1, 'a', 'flac', 'flac', 16000, 1) RETURNING id"
                     ).fetchone()[0]
    db.execute("INSERT INTO job (kind, recording_id, status) VALUES ('x', %s, 'done')", (rid,))
    db.execute("DELETE FROM recording WHERE id=%s", (rid,))
    assert db.execute("SELECT count(*) FROM job").fetchone()[0] == 0


def test_runner_settings_defaults(tmp_path):
    s = make_settings(tmp_path)
    assert s.runner.heartbeat_timeout_s == 120 and s.runner.cancel_grace_s == 5


# ---- enqueue ------------------------------------------------------------------------------------------

def test_enqueue_signature_stores_steps_in_order(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        FakeStep(log, "fake").register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg, start=False) as e:
            jid = await e.svc.enqueue("test", None, ["fake", {"name": "fake", "params": {"n": 2}}], {"n": 1})
            j = await e.svc.get(jid)
            assert j["steps"] == [{"name": "fake", "params": {}}, {"name": "fake", "params": {"n": 2}}]
            assert j["params"] == {"n": 1} and j["status"] == "queued" and j["kind"] == "test"
    run(go)


def test_unknown_step_rejected_no_row(db, db_url, mk_settings):
    async def go():
        async with jobs_env(db_url, mk_settings, start=False) as e:
            with pytest.raises(JobError, match="nope"):
                await e.svc.enqueue("test", None, ["nope"])
            assert await e.svc.list() == []
    run(go)


# ---- running ------------------------------------------------------------------------------------------

def test_fifo_one_at_a_time_in_order(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        FakeStep(log, "s1", sleep=0.05).register(reg)
        FakeStep(log, "s2", sleep=0.05).register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg, start=False) as e:
            j1 = await e.svc.enqueue("t", None, ["s1", "s2"])
            j2 = await e.svc.enqueue("t", None, ["s1", "s2"])
            await e.runner.start()
            await wait_status(e.svc, j2, "done")
            assert await wait_status(e.svc, j1, "done")
        assert log == [("start", j1, "s1"), ("end", j1, "s1"), ("start", j1, "s2"), ("end", j1, "s2"),
                       ("start", j2, "s1"), ("end", j2, "s1"), ("start", j2, "s2"), ("end", j2, "s2")]
    run(go)


def test_progress_and_step_reported(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        FakeStep(log, "slow", sleep=1.0).register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("t", None, ["slow"])
            j = await wait_until(lambda: _prog(e, jid), what="progress 0.5")
            assert j["status"] == "running" and j["step"] == "slow" and j["step_progress"] == 0.5
            assert j["message"] == "slow half"
            done = await wait_status(e.svc, jid, "done")
            assert done["finished_at"] and done["attempt"] == 1
    run(go)


async def _prog(e, jid):
    j = await e.svc.get(jid)
    return j if j["step_progress"] == 0.5 else None


def test_failure_stops_job_no_auto_retry(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        a, bad, c = (FakeStep(log, "a"), FakeStep(log, "bad", fail=99), FakeStep(log, "c"))
        for s in (a, bad, c):
            s.register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("t", None, ["a", "bad", "c"])
            j = await wait_status(e.svc, jid, "failed")
            await asyncio.sleep(0.3)
            assert j["step"] == "bad" and "bad boom" in j["error"] and "bad" in j["error"]
            assert (await e.svc.get(jid))["status"] == "failed"
        assert (a.calls, bad.calls, c.calls) == (1, 1, 0)
    run(go)


def test_retry_resumes_at_failed_step(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        a, b, c = (FakeStep(log, "a"), FakeStep(log, "b", fail=1), FakeStep(log, "c"))
        for s in (a, b, c):
            s.register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("t", None, ["a", "b", "c"])
            await wait_status(e.svc, jid, "failed")
            r = await e.svc.retry(jid)
            assert r["status"] == "queued" and r["error"] is None
            await wait_status(e.svc, jid, "done")
        assert (a.calls, b.calls, c.calls) == (1, 2, 1)
    run(go)


def test_done_marker_atomic_no_tmp(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        FakeStep(log, "a", returns={"k": 1}).register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("t", None, ["a"])
            await wait_status(e.svc, jid, "done")
            d = job_dir(e, jid, "a")
            assert json.loads((d / "done.json").read_text())["result"] == {"k": 1}
            assert [p.name for p in d.iterdir() if p.name.endswith(".tmp")] == []
    run(go)


def test_restart_requeues_running_and_skips_done_steps(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        a, b, c = (FakeStep(log, "a"), FakeStep(log, "b"), FakeStep(log, "c"))
        for s in (a, b, c):
            s.register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg, start=False) as e:
            jid = await e.svc.enqueue("t", None, ["a", "b", "c"])
            # simulate: server died while b ran; a finished (marker on disk, index advanced)
            d = job_dir(e, jid, "a")
            d.mkdir(parents=True)
            (d / "done.json").write_text(json.dumps({"index": 0}))
            async with e.pool.connection() as c_:
                await c_.execute("UPDATE job SET status='running', step='b', step_index=1 WHERE id=%s", (jid,))
            await e.runner.start()
            await wait_status(e.svc, jid, "done")
        assert (a.calls, b.calls, c.calls) == (0, 1, 1)
    run(go)


def test_marker_without_index_advance_is_skipped(db, db_url, mk_settings):
    """Crash between writing done.json and advancing step_index: the step is not run twice."""
    async def go():
        log, reg = [], StepRegistry()
        a, b = FakeStep(log, "a"), FakeStep(log, "b")
        a.register(reg), b.register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg, start=False) as e:
            jid = await e.svc.enqueue("t", None, ["a", "b"])
            d = job_dir(e, jid, "a")
            d.mkdir(parents=True)
            (d / "done.json").write_text(json.dumps({"index": 0}))
            await e.runner.start()
            await wait_status(e.svc, jid, "done")
        assert (a.calls, b.calls) == (0, 1)
    run(go)


def test_same_step_name_twice_runs_twice(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        f = FakeStep(log, "fake").register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("t", None, ["fake", "fake"])
            await wait_status(e.svc, jid, "done")
        assert f.calls == 2
    run(go)


def test_unknown_step_at_run_time_fails_naming_it(db, db_url, mk_settings):
    async def go():
        async with jobs_env(db_url, mk_settings) as e:
            async with e.pool.connection() as c:
                cur = await c.execute("INSERT INTO job (kind, status, steps) VALUES ('t', 'queued', "
                                      "'[{\"name\": \"ghost\", \"params\": {}}]') RETURNING id")
                jid = str((await cur.fetchone())["id"])
            e.runner.wake()
            j = await wait_status(e.svc, jid, "failed")
            assert "ghost" in j["error"]
    run(go)


def test_step_context_fields_and_param_merge(db, db_url, mk_settings):
    async def go():
        seen, reg = {}, StepRegistry()

        @reg.register("probe")
        async def probe(ctx):
            seen["ctx"] = ctx
            seen["is_dir"] = ctx.step_dir.is_dir()

        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("t", None, [{"name": "probe", "params": {"b": 2}}], {"a": 1, "b": 1})
            await wait_status(e.svc, jid, "done")
            ctx = seen["ctx"]
            for f in ("job_id", "step_name", "params", "cancelled", "step_dir", "data_dir", "settings", "memory",
                      "progress", "spawn_child"):
                assert hasattr(ctx, f), f
            assert ctx.params == {"a": 1, "b": 2} and ctx.step_name == "probe" and str(ctx.job_id) == jid
            assert ctx.step_dir == e.data_dir / "jobs" / jid / "probe" and seen["is_dir"]
            assert isinstance(ctx.cancelled, asyncio.Event) and ctx.memory is e.memory
            assert ctx.data_dir == e.data_dir and ctx.settings is e.settings
    run(go)


# ---- cancel -------------------------------------------------------------------------------------------

def spawn_step(reg, seen, mode="beat", seconds=60):
    @reg.register("child")
    async def child(ctx):
        (ctx.step_dir / "partial.txt").write_text("keep me")

        def on_line(line):
            if line.startswith("pid "):
                seen["pid"] = int(line.split()[1])
        await ctx.spawn_child(child_argv(mode, seconds), on_line=on_line)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_cancel_kills_real_child_keeps_partial_files(db, db_url, mk_settings):
    async def go():
        seen, reg = {}, StepRegistry()
        spawn_step(reg, seen)
        async with jobs_env(db_url, mk_settings, steps=reg, grace="1") as e:
            jid = await e.svc.enqueue("t", None, ["child"])
            await wait_until(lambda: "pid" in seen, what="child started")
            assert alive(seen["pid"])
            r = await e.svc.cancel(jid)
            assert r["status"] == "cancelling"
            await wait_until(lambda: not alive(seen["pid"]), timeout=2.5, what="child gone")
            j = await wait_status(e.svc, jid, "cancelled", timeout=3)
            assert j["finished_at"] and (job_dir(e, jid, "child") / "partial.txt").read_text() == "keep me"
            assert not (job_dir(e, jid, "child") / "done.json").exists()
    run(go)


def test_cancel_running_python_step_without_child(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        FakeStep(log, "slow", sleep=30).register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg, grace="1") as e:
            jid = await e.svc.enqueue("t", None, ["slow"])
            await wait_until(lambda: log, what="step started")
            await e.svc.cancel(jid)
            await wait_status(e.svc, jid, "cancelled", timeout=4)
    run(go)


def test_cancel_queued_job_never_runs(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        FakeStep(log, "a").register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg, start=False) as e:
            jid = await e.svc.enqueue("t", None, ["a"])
            assert (await e.svc.cancel(jid))["status"] == "cancelled"
            await e.runner.start()
            await asyncio.sleep(0.4)
            assert log == [] and (await e.svc.get(jid))["status"] == "cancelled"
    run(go)


def test_wrong_state_and_unknown_job(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        FakeStep(log, "a").register(reg)
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("t", None, ["a"])
            await wait_status(e.svc, jid, "done")
            with pytest.raises(WrongState):
                await e.svc.cancel(jid)
            with pytest.raises(WrongState):
                await e.svc.retry(jid)
            assert (await e.svc.get(jid))["status"] == "done"
            with pytest.raises(NotFound):
                await e.svc.cancel("00000000-0000-0000-0000-000000000000")
    run(go)


# ---- heartbeat ----------------------------------------------------------------------------------------

def test_silent_child_killed_and_job_fails(db, db_url, mk_settings):
    async def go():
        seen, reg = {}, StepRegistry()
        spawn_step(reg, seen, mode="silent", seconds=5)
        async with jobs_env(db_url, mk_settings, steps=reg, heartbeat="1", grace="1") as e:
            jid = await e.svc.enqueue("t", None, ["child"])
            j = await wait_status(e.svc, jid, "failed", timeout=5)
            assert "no heartbeat" in j["error"] and "child" in j["error"] and j["step"] == "child"
    run(go)


def test_beating_child_is_not_killed_and_job_done(db, db_url, mk_settings):
    async def go():
        seen, reg = {}, StepRegistry()
        spawn_step(reg, seen, mode="beat", seconds=1.5)
        async with jobs_env(db_url, mk_settings, steps=reg, heartbeat="1") as e:
            jid = await e.svc.enqueue("t", None, ["child"])
            await wait_status(e.svc, jid, "done", timeout=6)
    run(go)


def test_child_nonzero_exit_fails_step(db, db_url, mk_settings):
    async def go():
        reg = StepRegistry()

        @reg.register("bad")
        async def bad(ctx):
            await ctx.spawn_child(["/bin/sh", "-c", "echo hi; exit 3"])
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("t", None, ["bad"])
            j = await wait_status(e.svc, jid, "failed")
            assert "exit" in j["error"] and "3" in j["error"]
    run(go)


# ---- on_terminal --------------------------------------------------------------------------------------

def test_finalizer_runs_on_failed(db, db_url, mk_settings):
    async def go():
        log, reg, got = [], StepRegistry(), []
        FakeStep(log, "probe", fail=9).register(reg)

        @reg.on_terminal("probe")
        async def fin(job, status, error):
            got.append((str(job["id"]), status, error))
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("t", None, ["probe"])
            await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: got, what="finalizer")
            assert len(got) == 1 and got[0][0] == jid and got[0][1] == "failed" and "probe boom" in got[0][2]
    run(go)


def test_finalizer_runs_on_cancel_while_running(db, db_url, mk_settings):
    async def go():
        log, reg, got = [], StepRegistry(), []
        FakeStep(log, "probe", sleep=30).register(reg)

        @reg.on_terminal("probe")
        async def fin(job, status, error):
            got.append(status)
        async with jobs_env(db_url, mk_settings, steps=reg, grace="1") as e:
            jid = await e.svc.enqueue("t", None, ["probe"])
            await wait_until(lambda: log, what="step started")
            await e.svc.cancel(jid)
            await wait_status(e.svc, jid, "cancelled", timeout=4)
            await wait_until(lambda: got, what="finalizer")
            assert got == ["cancelled"]
    run(go)


def test_finalizer_runs_on_cancel_while_queued_for_unrun_steps(db, db_url, mk_settings):
    async def go():
        log, reg, got = [], StepRegistry(), []
        FakeStep(log, "probe").register(reg)
        FakeStep(log, "other").register(reg)

        @reg.on_terminal("probe")
        async def fin(job, status, error):
            got.append(status)

        @reg.on_terminal("other")
        async def fin2(job, status, error):
            got.append("other:" + status)
        async with jobs_env(db_url, mk_settings, steps=reg, start=False) as e:
            jid = await e.svc.enqueue("t", None, ["probe", "other"])
            await e.svc.cancel(jid)
            assert got == ["cancelled", "other:cancelled"] and log == []
    run(go)


def test_finalizer_exception_does_not_change_status(db, db_url, mk_settings):
    async def go():
        log, reg = [], StepRegistry()
        FakeStep(log, "probe").register(reg)

        @reg.on_terminal("probe")
        async def fin(job, status, error):
            raise RuntimeError("finalizer blew up")
        async with jobs_env(db_url, mk_settings, steps=reg, start=False) as e:
            jid = await e.svc.enqueue("t", None, ["probe"])
            r = await e.svc.cancel(jid)
            assert r["status"] == "cancelled" and (await e.svc.get(jid))["status"] == "cancelled"
    run(go)


def test_finalizer_not_called_for_done_job(db, db_url, mk_settings):
    async def go():
        log, reg, got = [], StepRegistry(), []
        FakeStep(log, "probe").register(reg)
        reg.on_terminal("probe")(lambda job, status, error: got.append(status))
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("t", None, ["probe"])
            await wait_status(e.svc, jid, "done")
            await asyncio.sleep(0.2)
            assert got == []
    run(go)
