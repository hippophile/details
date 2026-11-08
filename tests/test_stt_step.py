"""stt-runs: stt_transcribe step + terminal finalizer on the real runner with the fake engine child."""
import asyncio, json, os

import pytest

from details.runner import runner as runner_mod
from details.runner import steps_stt
from details.runner.steps import StepRegistry
from details.services import stt_runs
from jobs_helpers import jobs_env, wait_status, wait_until
from stt_helpers import fake_worker_env, rec, run

PLUGIN = {"DETAILS_STT_PLUGINS": "fakes.stt"}


async def start(e, rid, **kw):
    reg = e.models
    return await stt_runs.start_run(e.pool, e.svc, reg, e.settings, rid, kw.pop("model_id", "whisper-turbo"),
                                    language=kw.pop("language", "el"), vocab_hint=kw.pop("vocab_hint", True),
                                    window_s=kw.pop("window_s", 1))


def env(db_url, mk_settings, **kw):
    reg = StepRegistry()
    steps_stt.install(reg)
    return jobs_env(db_url, mk_settings, steps=reg, **PLUGIN, **kw)


def row(db, run_id):
    return db.execute("SELECT status, error, text, raw, wall_ms, audio_ms, rtf, language FROM stt_run WHERE id=%s",
                      (run_id,)).fetchone()


def test_step_stores_segments_postfixed_with_timing_and_raw(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_worker_env(monkeypatch, tmp_path)
    s = mk_settings(db_url)
    rid = rec(db, s, seconds=5)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_stt.bind(e.pool)
            run_id, jid = await start(e, rid)
            await wait_status(e.svc, jid, "done")
            return e.data_dir, jid, run_id
    data_dir, jid, run_id = run(go)
    status, error, text, raw, wall, audio, rtf, lang = row(db, run_id)
    assert status == "done" and error is None and lang == "el"
    assert text == "w0 Τι κάνεις; w1 Τι κάνεις; w2 Τι κάνεις; w3 Τι κάνεις; w4 Τι κάνεις;"
    assert "Τι κάνεις?" in json.dumps(raw, ensure_ascii=False) and "Τι κάνεις;" not in json.dumps(raw, ensure_ascii=False)
    assert audio == 5000 and wall > 0 and rtf == pytest.approx(5.0 / (wall / 1000), rel=0.01)
    segs = db.execute("SELECT idx, start_ms, end_ms, text FROM transcript_segment WHERE stt_run_id=%s ORDER BY idx",
                      (run_id,)).fetchall()
    assert [x[0] for x in segs] == [0, 1, 2, 3, 4] and segs[1][1:3] == (1000, 2000) and segs[1][3] == "w1 Τι κάνεις;"
    found = list(data_dir.rglob("segments.jsonl"))       # the checkpoint lives in step_dir and nowhere else
    assert found == [data_dir / "jobs" / jid / "stt_transcribe" / "segments.jsonl"]


def test_engine_receives_run_settings(db, db_url, mk_settings, tmp_path, monkeypatch):
    calls = fake_worker_env(monkeypatch, tmp_path)
    s = mk_settings(db_url)
    rid = rec(db, s, seconds=1)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_stt.bind(e.pool)
            _, jid = await start(e, rid, language="el", vocab_hint=True)
            await wait_status(e.svc, jid, "done")
            return e.settings.stt.vocab_hint
    hint = run(go)
    got = json.loads(calls.read_text().splitlines()[0])["settings"]
    assert got["language"] == "el" and got["initial_prompt"] == hint and got["condition_on_previous_text"] is False


def test_memory_released_before_child_spawn(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_worker_env(monkeypatch, tmp_path)
    s = mk_settings(db_url)
    rid = rec(db, s, seconds=1)
    order = []
    real = runner_mod.run_child

    async def spy(*a, **k):
        order.append("spawn")
        return await real(*a, **k)
    monkeypatch.setattr(runner_mod, "run_child", spy)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_stt.bind(e.pool)

            async def release():
                order.append("release")
            e.memory.release_for_stt = release
            _, jid = await start(e, rid)
            await wait_status(e.svc, jid, "done")
    run(go)
    assert order == ["release", "spawn"]


def test_child_death_fails_run_keeps_checkpoint_and_no_segments(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_worker_env(monkeypatch, tmp_path, die_at=2)
    s = mk_settings(db_url)
    rid = rec(db, s, seconds=5)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_stt.bind(e.pool)
            run_id, jid = await start(e, rid)
            await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: row(db, run_id)[0] == "failed", what="run failed")
            return e.data_dir, jid, run_id
    data_dir, jid, run_id = run(go)
    status, error = row(db, run_id)[:2]
    assert status == "failed" and error
    assert db.execute("SELECT count(*) FROM transcript_segment").fetchone()[0] == 0
    ck = data_dir / "jobs" / jid / "stt_transcribe" / "segments.jsonl"
    assert len(ck.read_text().splitlines()) == 2        # intact, ready for a retry


def test_retry_resumes_the_same_job(db, db_url, mk_settings, tmp_path, monkeypatch):
    calls = fake_worker_env(monkeypatch, tmp_path, die_at=2)
    s = mk_settings(db_url)
    rid = rec(db, s, seconds=4)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_stt.bind(e.pool)
            run_id, jid = await start(e, rid)
            await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: row(db, run_id)[0] == "failed", what="run failed")
            monkeypatch.delenv("FAKE_STT_DIE_AT")
            calls.unlink()
            await e.svc.retry(jid)
            await wait_status(e.svc, jid, "done")
            return run_id
    run_id = run(go)
    assert [json.loads(x)["start_s"] for x in calls.read_text().splitlines()] == [2, 3]
    assert row(db, run_id)[0] == "done"
    assert db.execute("SELECT count(*) FROM transcript_segment").fetchone()[0] == 4


def test_cancel_running_kills_child_and_marks_cancelled(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_worker_env(monkeypatch, tmp_path, delay=0.4)
    pidfile = tmp_path / "pid"
    monkeypatch.setenv("FAKE_STT_PIDFILE", str(pidfile))
    s = mk_settings(db_url)
    rid = rec(db, s, seconds=60)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_stt.bind(e.pool)
            run_id, jid = await start(e, rid)
            await wait_until(lambda: pidfile.exists() and pidfile.read_text(), what="child started")
            pid = int(pidfile.read_text())
            await e.svc.cancel(jid)
            await wait_status(e.svc, jid, "cancelled")
            await wait_until(lambda: row(db, run_id)[0] == "cancelled", what="run cancelled")
            return pid
    pid = run(go)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cancelled_while_queued_marks_run_cancelled(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_worker_env(monkeypatch, tmp_path)
    s = mk_settings(db_url)
    rid = rec(db, s)

    async def go():
        async with env(db_url, mk_settings, start=False) as e:      # runner never claims the job
            steps_stt.bind(e.pool)
            run_id, jid = await start(e, rid)
            assert row(db, run_id)[0] == "queued"
            await e.svc.cancel(jid)
            return run_id
    run_id = run(go)
    assert row(db, run_id)[0] == "cancelled"


def test_step_that_raises_fails_run_with_error_text(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_worker_env(monkeypatch, tmp_path)
    s = mk_settings(db_url)
    rid = rec(db, s)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_stt.bind(e.pool)
            run_id, jid = await start(e, rid)
            db.execute("DELETE FROM stt_run WHERE id=%s", (run_id,))     # the step cannot find its run
            await wait_status(e.svc, jid, "failed")
    run(go)       # finalizer on a vanished run must not raise


def test_finalizer_sets_failed_with_error_directly(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = rec(db, s)
    from stt_helpers import insert_run
    run_id = insert_run(db, rid, status="running")
    job = {"id": "j", "params": {}, "steps": [{"name": "stt_transcribe", "params": {"stt_run_id": run_id}}]}

    async def go():
        from details.db import open_pool
        pool = await open_pool(db_url)
        steps_stt.bind(pool)
        try:
            await steps_stt.finalize(job, "failed", "stt_transcribe: boom")
        finally:
            await pool.close()
    asyncio.run(go())
    assert row(db, run_id)[:2] == ("failed", "stt_transcribe: boom")


def test_registered_fake_engine_is_used_by_name(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_worker_env(monkeypatch, tmp_path, override=False)
    s = mk_settings(db_url)
    rid = rec(db, s, seconds=2)
    from stt_helpers import insert_run
    run_id = insert_run(db, rid, status="queued", engine="fake", model_id="whisper-turbo",
                        settings=json.dumps({"language": "el", "window_s": 1, "initial_prompt": None}))

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_stt.bind(e.pool)
            jid = await e.svc.enqueue("stt", rid, [{"name": "stt_transcribe", "params": {"stt_run_id": run_id}}])
            await wait_status(e.svc, jid, "done")
    run(go)
    assert row(db, run_id)[2] == "w0 Τι κάνεις; w1 Τι κάνεις;"


def test_nan_logprob_is_stored_as_null_not_a_failed_run(db, db_url, mk_settings, tmp_path, monkeypatch):
    """Whisper turbo returns avg_logprob=NaN for a lone "!" in silence; Postgres JSON rejects NaN, which failed the
    whole run on save. A non-finite score is no score: NULL in the column and null in raw."""
    fake_worker_env(monkeypatch, tmp_path, nan=1)
    s = mk_settings(db_url)
    rid = rec(db, s, seconds=3)

    async def go():
        async with env(db_url, mk_settings) as e:
            steps_stt.bind(e.pool)
            run_id, jid = await start(e, rid)
            await wait_status(e.svc, jid, "done")
            return run_id
    run_id = run(go)
    status, error, _, raw, *_ = row(db, run_id)
    assert status == "done" and error is None
    assert db.execute("SELECT count(*), count(avg_logprob) FROM transcript_segment WHERE stt_run_id=%s",
                      (run_id,)).fetchone() == (3, 0)
    assert all(seg["avg_logprob"] is None for w in raw["windows"] for seg in w["segments"])
