"""run-insights terminal unload (folded in): a failed/cancelled analysis unloads the app's own LM Studio model,
with guards; a retry reloads it. Fakes only."""
import asyncio, contextlib

from details.llm.lmstudio_ctl import LMStudioError, identifier_for
from details.runner import steps_analyse
from details.runner.steps import StepRegistry
from fakes.lmstudio import FakeLMStudio
from jobs_helpers import jobs_env, run, wait_status, wait_until
from llm_helpers import good_responder, seed_run, sync_prompts
from test_llm_steps import start

MINE = identifier_for("gemma-cheap")
USER = {"identifier": "google/gemma-4-e4b", "modelKey": "google/gemma-4-e4b"}
BOOM = {"status": 500, "error": "model crashed"}


@contextlib.asynccontextmanager
async def term_env(db_url, mk_settings, fake, **env):
    url = await fake.start_http()
    reg = StepRegistry()
    steps_analyse.install(reg)

    async def slow(ctx):
        await asyncio.sleep(30)
    reg.register("slow")(slow)
    try:
        async with jobs_env(db_url, mk_settings, steps=reg, lm=fake, DETAILS_LMSTUDIO_URL=url, **env) as e:
            steps_analyse.bind(e.pool, memory=e.memory, settings=e.settings, lease=e.runner.lease)
            yield e
    finally:
        await fake.stop_http()


def idents(fake):
    return [m["identifier"] for m in fake.loaded]


def unloads(fake):
    return [c for c in fake.calls if c[0] == "unload"]


def test_failed_analysis_unloads_only_details_models(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio(loaded=[USER])
    fake.script(BOOM)

    async def body():
        async with term_env(db_url, mk_settings, fake) as e:
            gid, jid = await start(e, run_id, ["tags"])
            await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: unloads(fake), what="terminal unload")
    asyncio.run(asyncio.wait_for(body(), 40))
    assert [c[0] for c in fake.calls] == ["load", "unload"] and fake.calls[1] == ("unload", MINE)
    assert idents(fake) == ["google/gemma-4-e4b"]                       # the user's model is untouched


def test_cancelled_while_running_unloads(db, db_url, mk_settings):
    sync_prompts(db_url)
    fake = FakeLMStudio()

    async def body():
        async with term_env(db_url, mk_settings, fake) as e:
            jid = await e.svc.enqueue("analyse", None, [{"name": "lm_load", "params": {"model_id": "gemma-cheap"}},
                                                        "slow", "analyse", "lm_unload"],
                                      {"group_id": "00000000-0000-0000-0000-0000000000aa"})
            await wait_until(lambda: _step_is(e, jid, "slow"), what="slow running")
            await e.svc.cancel(jid)
            await wait_status(e.svc, jid, "cancelled")
            await wait_until(lambda: unloads(fake), what="terminal unload")      # the finalizer runs after the status flips
    asyncio.run(asyncio.wait_for(body(), 40))
    assert unloads(fake) == [("unload", MINE)] and idents(fake) == []


async def _step_is(e, jid, name):
    j = await e.svc.get(jid)
    return j["status"] == "running" and j["step"] == name


def test_queued_cancel_while_another_job_runs_does_not_unload(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio(loaded=[{"identifier": MINE, "modelKey": "x"}])

    async def body():
        async with term_env(db_url, mk_settings, fake) as e:
            a = await e.svc.enqueue("test", None, ["slow"], {})
            await wait_until(lambda: _step_is(e, a, "slow"), what="A running")
            gid, b = await start(e, run_id, ["tags"])
            await e.svc.cancel(b)
            assert (await e.svc.get(b))["status"] == "cancelled"
            await e.svc.cancel(a)
            await wait_status(e.svc, a, "cancelled")
    asyncio.run(asyncio.wait_for(body(), 40))
    assert unloads(fake) == [] and idents(fake) == [MINE]


def test_queued_cancel_while_lease_owner_is_live_does_not_unload(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio(loaded=[{"identifier": MINE, "modelKey": "x"}])

    async def body():
        async with term_env(db_url, mk_settings, fake, ) as e:
            await e.runner.stop()                          # nothing claims the job: it stays queued
            hold = e.runner.lease.acquire("live")
            await hold.wait()
            gid, b = await start(e, run_id, ["tags"])
            await e.svc.cancel(b)
            assert unloads(fake) == []
            hold.release()                                 # control: same cancel without the live owner unloads
            gid2, b2 = await start(e, run_id, ["tags"])
            await e.svc.cancel(b2)
    asyncio.run(asyncio.wait_for(body(), 40))
    assert unloads(fake) == [("unload", MINE)]


def test_a_queued_job_that_loads_the_same_model_keeps_it_loaded(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio(loaded=[{"identifier": MINE, "modelKey": "x"}])

    async def body():
        async with term_env(db_url, mk_settings, fake) as e:
            await e.runner.stop()
            gid, a = await start(e, run_id, ["tags"])
            gid2, b = await start(e, run_id, ["notes"])
            await e.svc.cancel(a)                          # b is queued and will lm_load the same model
    asyncio.run(asyncio.wait_for(body(), 40))
    assert unloads(fake) == []


class BrokenUnload(FakeLMStudio):
    async def unload(self, identifier):
        self.calls.append(("unload", identifier))
        raise LMStudioError("unload refused")


def test_unload_error_keeps_the_job_failed(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = BrokenUnload()
    fake.script(BOOM)

    async def body():
        async with term_env(db_url, mk_settings, fake) as e:
            gid, jid = await start(e, run_id, ["tags"])
            job = await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: unloads(fake), what="unload attempted")
            return gid, job
    gid, job = asyncio.run(asyncio.wait_for(body(), 40))
    assert job["status"] == "failed" and "500" in job["error"]
    assert db.execute("SELECT status FROM analysis_run WHERE group_id=%s", (gid,)).fetchone()[0] == "failed"


def test_unload_on_terminal_false_keeps_the_model(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.script(BOOM)

    async def body():
        async with term_env(db_url, mk_settings, fake, DETAILS_LMSTUDIO_UNLOAD_ON_TERMINAL="false") as e:
            gid, jid = await start(e, run_id, ["tags"])
            await wait_status(e.svc, jid, "failed")
            await asyncio.sleep(0.3)
    asyncio.run(asyncio.wait_for(body(), 40))
    assert unloads(fake) == [] and idents(fake) == [MINE]


def test_retry_after_a_failure_reloads_the_model_and_finishes(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.script(BOOM)
    fake.responder = good_responder

    async def body():
        async with term_env(db_url, mk_settings, fake) as e:
            gid, jid = await start(e, run_id, ["tags"])
            await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: unloads(fake), what="terminal unload")
            assert idents(fake) == []
            await e.svc.retry(jid)
            await wait_status(e.svc, jid, "done")
    asyncio.run(asyncio.wait_for(body(), 40))
    assert [c[0] for c in fake.calls] == ["load", "unload", "load", "unload"]
