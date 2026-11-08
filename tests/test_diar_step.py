"""speakers-diarization: the `diarize` step + terminal finalizer on the real runner with the fake engine child."""
import asyncio, json, os, sys

import pytest

from details.runner import runner as runner_mod
from details.runner import steps_diar
from details.runner.joblog import JobLogs
from details.runner.steps import StepRegistry
from details.services import diarization
from diar_helpers import PLUGIN, SENTINEL, TOKEN_NAMES, drow, fake_diar_env, lines, queue_run
from jobs_helpers import jobs_env, wait_status, wait_until
from stt_helpers import rec, run

ENV = "/usr/bin/env"


def env(db_url, mk_settings, **kw):
    reg = StepRegistry()
    steps_diar.install(reg)
    return jobs_env(db_url, mk_settings, steps=reg, **PLUGIN, **kw)


def turns(db, run_id):
    return db.execute("SELECT idx, start_ms, end_ms, speaker FROM diar_turn WHERE diarization_run_id=%s ORDER BY idx",
                      (run_id,)).fetchall()


def test_install_registers_the_step_and_its_finalizer():
    reg = StepRegistry()
    steps_diar.install(reg)
    assert reg.has("diarize") and steps_diar.finalize in reg.finalizers("diarize")


def test_default_registry_has_the_step_after_import():
    from details.runner.steps import steps
    assert steps.has("diarize")


def test_step_stores_turns_and_numbers_by_code(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, turns=json.dumps([
        {"start_ms": 0, "end_ms": 6000, "speaker": "SPEAKER_01"}, {"start_ms": 6000, "end_ms": 9000, "speaker": "SPEAKER_00"},
        {"start_ms": 9000, "end_ms": 10000, "speaker": "SPEAKER_01"}]))
    rid = rec(db, mk_settings(db_url), seconds=10)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid)
            await wait_status(e.svc, jid, "done")
            return run_id, jid, e.data_dir
    run_id, jid, data_dir = run(go)
    status, error, started, wall, audio, rtf, speakers = drow(db, run_id)
    assert status == "done" and error is None and started is not None and wall > 0 and audio == 10000
    assert rtf == pytest.approx(10.0 / (wall / 1000), rel=0.01)
    assert speakers == [{"speaker": 1, "talk_ms": 7000, "share_pct": 70.0, "turns": 2},
                        {"speaker": 2, "talk_ms": 3000, "share_pct": 30.0, "turns": 1}]
    assert turns(db, run_id) == [(0, 0, 6000, 1), (1, 6000, 9000, 2), (2, 9000, 10000, 1)]
    assert sorted(p.name for p in (data_dir / "jobs" / jid / "diarize").glob("*.json")) == [
        "done.json", "spec.json", "step.json", "turns.json", "worker.done.json"]


def test_zero_turns_is_done_with_an_empty_speakers_list(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, turns="[]")
    rid = rec(db, mk_settings(db_url), seconds=10)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid)
            await wait_status(e.svc, jid, "done")
            return run_id
    run_id = run(go)
    assert drow(db, run_id)[0] == "done" and drow(db, run_id)[6] == [] and turns(db, run_id) == []


def test_ingest_is_idempotent(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url), seconds=10)

    async def go():
        async with env(db_url, mk_settings, start=False) as e:
            steps_diar.bind(e.pool)
            run_id, _ = await queue_run(e, rid)
            raw = [{"start_ms": 0, "end_ms": 6000, "speaker": "A"}, {"start_ms": 6000, "end_ms": 9000, "speaker": "B"}]
            for _ in range(2):
                await diarization.store_result(e.pool, run_id, raw, audio_ms=10000, merge_gap_ms=1500, wall_ms=5000)
            return run_id
    run_id = run(go)
    assert turns(db, run_id) == [(0, 0, 6000, 1), (1, 6000, 9000, 2)] and drow(db, run_id)[0] == "done"


def test_memory_released_before_child_spawn_and_argv_is_the_env_wrapper(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path)
    rid = rec(db, mk_settings(db_url), seconds=1)
    order, argvs = [], []
    real = runner_mod.run_child

    async def spy(argv, **k):
        order.append("spawn")
        argvs.append(list(argv))
        return await real(argv, **k)
    monkeypatch.setattr(runner_mod, "run_child", spy)

    async def go():
        async with env(db_url, mk_settings, HF_TOKEN=SENTINEL) as e:
            steps_diar.bind(e.pool)

            async def release():
                order.append("release")
            e.memory.release_for_stt = release
            run_id, jid = await queue_run(e, rid)
            await wait_status(e.svc, jid, "done")
            return e.settings, e.data_dir, jid
    settings, data_dir, jid = run(go)
    assert order == ["release", "spawn"]
    a = argvs[0]
    assert a[:7] == [ENV, "-u", "HF_TOKEN", "-u", "HUGGING_FACE_HUB_TOKEN", "-u", "DETAILS_DIARIZATION_HF_TOKEN"]
    assert a[7:9] == ["HF_HUB_OFFLINE=1", f"HF_HUB_CACHE={settings.paths.hf_hub}"]
    assert a[9:12] == [sys.executable, "-m", "details.engines.diar_worker"] and a[12].endswith("/diarize/spec.json")
    spec = (data_dir / "jobs" / jid / "diarize" / "spec.json").read_text()
    assert settings.diarization.hf_token == SENTINEL
    assert SENTINEL not in spec and SENTINEL not in " ".join(a)


def test_worker_environment_is_offline_and_token_free(db, db_url, mk_settings, tmp_path, monkeypatch):
    paths = fake_diar_env(monkeypatch, tmp_path)
    for n in TOKEN_NAMES:
        monkeypatch.setenv(n, SENTINEL)                 # the server process has all three set
    rid = rec(db, mk_settings(db_url), seconds=1)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_diar.bind(e.pool)
            _, jid = await queue_run(e, rid)
            await wait_status(e.svc, jid, "done")
            return e.settings.paths.hf_hub
    hub = run(go)
    got = json.loads(paths.envfile.read_text())
    assert got["HF_HUB_OFFLINE"] == "1" and got["HF_HUB_CACHE"] == str(hub) and got["tokens"] == {n: False for n in TOKEN_NAMES}


def test_device_and_hints_reach_the_engine_through_the_step(db, db_url, mk_settings, tmp_path, monkeypatch):
    paths = fake_diar_env(monkeypatch, tmp_path)
    rid = rec(db, mk_settings(db_url), seconds=1)

    async def go():
        async with env(db_url, mk_settings, DETAILS_DIARIZATION_DEVICE="mps") as e:
            steps_diar.bind(e.pool)
            _, jid = await queue_run(e, rid, min_speakers=2, max_speakers=3)
            await wait_status(e.svc, jid, "done")
    run(go)
    assert lines(paths.calls) == [{"device": "mps", "min_speakers": 2, "max_speakers": 3}]
    assert json.loads(paths.envfile.read_text())["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"


def test_run_starts_clears_old_error_and_keeps_started_at(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, delay=1.0)
    rid = rec(db, mk_settings(db_url), seconds=5)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid, status="failed")
            db.execute("UPDATE diarization_run SET error='old failure', started_at='2026-01-01T00:00:00+00:00' "
                       "WHERE id=%s", (run_id,))
            await wait_until(lambda: drow(db, run_id)[0] == "running", what="running")
            mid = drow(db, run_id)
            await wait_status(e.svc, jid, "done")
            return run_id, mid
    run_id, mid = run(go)
    assert mid[1] is None and mid[2].year == 2026 and mid[2].month == 1
    assert drow(db, run_id)[0] == "done"


def test_retry_after_failure_ends_done_and_the_finalizer_never_overwrites_done(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, fail="first try fails")
    rid = rec(db, mk_settings(db_url), seconds=66)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid)
            await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: drow(db, run_id)[0] == "failed", what="run failed")
            monkeypatch.delenv("FAKE_DIAR_FAIL")
            await e.svc.retry(jid)
            await wait_status(e.svc, jid, "done")
            job = await e.svc.get(jid)
            await steps_diar.finalize({**job, "steps": job["steps"]}, "failed", "late finalizer")
            return run_id
    run_id = run(go)
    st, error = drow(db, run_id)[:2]
    assert st == "done" and error is None and len(turns(db, run_id)) == 3


def test_missing_run_row_is_a_plain_error(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path)
    rid = rec(db, mk_settings(db_url), seconds=1)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_diar.bind(e.pool)
            jid = await e.svc.enqueue("diarize", rid, [{"name": "diarize", "params": {
                "diarization_run_id": "00000000-0000-0000-0000-000000000000"}}])
            return await wait_status(e.svc, jid, "failed")
    assert "diarization run not found" in run(go)["error"]


def test_resume_without_rerunning_the_engine(db, db_url, mk_settings, tmp_path, monkeypatch):
    paths = fake_diar_env(monkeypatch, tmp_path)
    rid = rec(db, mk_settings(db_url), seconds=10)

    async def go():
        async with env(db_url, mk_settings, start=False) as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid)
            d = e.data_dir / "jobs" / jid / "diarize"
            d.mkdir(parents=True)
            (d / "turns.json").write_text(json.dumps({"turns": [{"start_ms": 0, "end_ms": 4000, "speaker": "Z"}]}))
            (d / "worker.done.json").write_text("{}")
            await e.runner.start()
            await wait_status(e.svc, jid, "done")
            return run_id
    run_id = run(go)
    assert not paths.calls.exists()                                    # the engine child was never started
    assert turns(db, run_id) == [(0, 0, 4000, 1)] and drow(db, run_id)[0] == "done"


def test_cancel_kills_the_child_and_ends_cancelled(db, db_url, mk_settings, tmp_path, monkeypatch):
    paths = fake_diar_env(monkeypatch, tmp_path, delay=30)
    monkeypatch.setenv("FAKE_DIAR_PIDFILE", str(paths.pidfile))
    rid = rec(db, mk_settings(db_url), seconds=60)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid)
            await wait_until(lambda: paths.pidfile.exists() and paths.pidfile.read_text(), what="child started")
            pid = int(paths.pidfile.read_text())
            await e.svc.cancel(jid)
            await wait_status(e.svc, jid, "cancelled")
            await wait_until(lambda: drow(db, run_id)[0] == "cancelled", what="run cancelled")
            return pid
    pid = run(go)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_worker_message_replaces_the_exit_code(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, fail="the model cannot load")
    rid = rec(db, mk_settings(db_url), seconds=5)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid)
            job = await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: drow(db, run_id)[0] == "failed", what="run failed")
            return run_id, job
    run_id, job = run(go)
    error = drow(db, run_id)[1]
    assert "the model cannot load" in error and "child exited" not in error and "the model cannot load" in job["error"]
    assert turns(db, run_id) == []


def test_without_an_error_file_the_runner_message_stays(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, stderr_token="not-a-secret")     # the fake exits 1 without writing error.json
    rid = rec(db, mk_settings(db_url), seconds=5)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid)
            await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: drow(db, run_id)[0] == "failed", what="run failed")
            return run_id
    assert "exited with code 1" in drow(db, run(go))[1]


def test_token_only_in_worker_stderr_is_masked_everywhere(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, stderr_token=SENTINEL)
    rid = rec(db, mk_settings(db_url), seconds=5)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid)
            await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: drow(db, run_id)[0] == "failed", what="run failed")
            d = e.data_dir / "jobs" / jid / "diarize"
            tail = JobLogs.for_settings(e.settings).get(jid).read()
            return run_id, d, tail
    run_id, d, tail = run(go)
    stderr = (d / "child.stderr.log").read_text()
    assert "401 Unauthorized" in stderr and SENTINEL not in stderr           # scrubbed in place, not just hidden
    assert SENTINEL not in json.dumps(tail) and any("401 Unauthorized" in x["msg"] for x in tail)
    assert SENTINEL not in (d / "step.json").read_text() and SENTINEL not in drow(db, run_id)[1]
    assert SENTINEL not in "".join(p.read_text(errors="replace") for p in d.rglob("*") if p.is_file())


def test_finalizer_cancels_a_queued_job_and_masks_tokens_in_errors(db, db_url, mk_settings, tmp_path, monkeypatch):
    rid = rec(db, mk_settings(db_url), seconds=5)

    async def go():
        async with env(db_url, mk_settings, start=False) as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid)
            await e.svc.cancel(jid)                                    # still queued: no step ever ran
            other, ojid = await queue_run(e, rid)
            job = await e.svc.get(ojid)
            await steps_diar.finalize(job, "failed", f"diarize: boom {SENTINEL}")
            return run_id, other
    run_id, other = run(go)
    assert drow(db, run_id)[0] == "cancelled"
    st, err = drow(db, other)[:2]
    assert st == "failed" and "boom" in err and SENTINEL not in err and "***" in err


def test_slow_model_load_beyond_the_heartbeat_timeout_does_not_kill_the_worker(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, load_delay=2.5)
    rid = rec(db, mk_settings(db_url), seconds=5)

    async def go():
        async with env(db_url, mk_settings, heartbeat="1.2", DETAILS_DIARIZATION_KEEPALIVE_S="0.3") as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid)
            await wait_status(e.svc, jid, "done", timeout=20)
            return run_id, JobLogs.for_settings(e.settings).get(jid).read()
    run_id, log = run(go)
    assert drow(db, run_id)[0] == "done" and sum(1 for x in log if x["src"] == "worker" and x["msg"] == "alive") >= 3


def test_hung_worker_stops_beating_after_stall_s_and_is_killed(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, hang=1)
    rid = rec(db, mk_settings(db_url), seconds=5)

    async def go():
        async with env(db_url, mk_settings, heartbeat="1.5", DETAILS_DIARIZATION_KEEPALIVE_S="0.2",
                       DETAILS_DIARIZATION_STALL_S="0.6") as e:
            steps_diar.bind(e.pool)
            run_id, jid = await queue_run(e, rid)
            job = await wait_status(e.svc, jid, "failed", timeout=20)
            await wait_until(lambda: drow(db, run_id)[0] == "failed", what="run failed")
            return run_id, job
    run_id, job = run(go)
    assert "no heartbeat" in drow(db, run_id)[1] and "no heartbeat" in job["error"]


def test_progress_lines_become_step_progress_naming_the_phase(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, callbacks=3, delay=2.0)
    rid = rec(db, mk_settings(db_url), seconds=5)

    async def go():
        async with env(db_url, mk_settings, DETAILS_DIARIZATION_PROGRESS_MIN_INTERVAL_S="0") as e:
            steps_diar.bind(e.pool)
            _, jid = await queue_run(e, rid)

            async def phase():
                j = await e.svc.get(jid)
                return j["message"] and "Speakers: embeddings" in j["message"] and j
            seen = await wait_until(phase, what="progress message")
            await wait_status(e.svc, jid, "done")
            return seen
    seen = run(go)
    assert seen["message"].startswith("Speakers: embeddings ") and seen["step_progress"] is not None


def test_diarize_holds_the_gpu_lease_so_live_waits_then_goes_before_the_next_job(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, delay=1.0)
    rid = rec(db, mk_settings(db_url), seconds=5)
    order = []

    async def go():
        reg = StepRegistry()
        steps_diar.install(reg)

        async def other(ctx):
            order.append("other")
        reg.register("other")(other)
        async with jobs_env(db_url, mk_settings, steps=reg, **PLUGIN) as e:
            steps_diar.bind(e.pool)
            _, jid = await queue_run(e, rid)
            await wait_until(lambda: e.runner.lease.owner == "job", what="diarize holds the lease")
            second = await e.svc.enqueue("test", None, ["other"], {})

            async def live():
                async with e.runner.lease.acquire("live"):
                    order.append("live")
                    await asyncio.sleep(0.3)
            t = asyncio.create_task(live())
            await asyncio.sleep(0.2)
            assert order == []                                          # live waits; the running step is not interrupted
            await wait_status(e.svc, jid, "done")
            await wait_status(e.svc, second, "done")
            await t
    run(go)
    assert order == ["live", "other"]
