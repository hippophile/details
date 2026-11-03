import pytest

from details.runner.steps import StepRegistry, steps as global_steps
from details.services.jobs import JobError
from fakes.lmstudio import FakeLMStudio
from jobs_helpers import jobs_env, run, wait_status


def test_global_registry_has_builtins():
    assert global_steps.has("lm_load") and global_steps.has("lm_unload")
    assert StepRegistry().has("lm_load")


def test_load_then_unload(db, db_url, mk_settings):
    async def go():
        lm = FakeLMStudio()
        async with jobs_env(db_url, mk_settings, lm=lm) as e:
            jid = await e.svc.enqueue("t", None, [{"name": "lm_load", "params": {"model_id": "gemma-cheap"}},
                                                  "lm_unload"])
            await wait_status(e.svc, jid, "done")
        assert lm.calls == [("load", "google/gemma-4-e4b", "details-gemma-cheap", 8192),
                            ("unload", "details-gemma-cheap")]
    run(go)


def test_model_id_from_job_params(db, db_url, mk_settings):
    async def go():
        lm = FakeLMStudio()
        async with jobs_env(db_url, mk_settings, lm=lm) as e:
            jid = await e.svc.enqueue("t", None, ["lm_load"], {"model_id": "gemma-nice"})
            await wait_status(e.svc, jid, "done")
        assert lm.calls[0][2] == "details-gemma-nice"
    run(go)


def test_lm_unload_leaves_foreign_models(db, db_url, mk_settings):
    async def go():
        lm = FakeLMStudio(loaded=[{"identifier": "google/gemma-4-e4b", "modelKey": "google/gemma-4-e4b"}])
        async with jobs_env(db_url, mk_settings, lm=lm) as e:
            jid = await e.svc.enqueue("t", None, ["lm_unload"])
            await wait_status(e.svc, jid, "done")
        assert lm.calls == []
    run(go)


def test_unknown_model_id_rejected_at_enqueue(db, db_url, mk_settings):
    async def go():
        async with jobs_env(db_url, mk_settings, start=False) as e:
            with pytest.raises(JobError, match="nope"):
                await e.svc.enqueue("t", None, [{"name": "lm_load", "params": {"model_id": "nope"}}])
            with pytest.raises(JobError, match="model_id"):
                await e.svc.enqueue("t", None, ["lm_load"])
            assert await e.svc.list() == []
    run(go)


def test_load_failure_fails_job(db, db_url, mk_settings):
    async def go():
        async with jobs_env(db_url, mk_settings, lm=FakeLMStudio(reachable=False)) as e:
            jid = await e.svc.enqueue("t", None, [{"name": "lm_load", "params": {"model_id": "gemma-cheap"}}])
            j = await wait_status(e.svc, jid, "failed")
            assert "lm_load" in j["error"] and "not reachable" in j["error"]
    run(go)
