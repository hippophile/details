"""LiveSession/LiveManager: lease paths, child lifecycle, restart-once, model checks, second session, busy worker."""
import asyncio, time

from live_helpers import (Msgs, Writer, feed, fake_rec, live_env, pid_alive, pids, read_calls, script_pcm,
                          wait_until)
from recording_helpers import sine_pcm
from stt_helpers import LARGE, TURBO


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


async def begin(e, tmp_path, name="rec1", silence_db=-45.0):
    live = e.manager.begin(fake_rec(tmp_path, name), silence_db)
    m = Msgs()
    live.attach(m.send)
    return live, m


PHRASE = script_pcm(("t", 1.0), ("s", 0.8))


# ---- lease -------------------------------------------------------------------------------------------------

def test_waits_for_a_running_job_step_then_starts_and_holds_the_lease(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        job = e.lease.acquire("job")
        await job
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: m.of("live_state", state="waiting_gpu"), what="waiting_gpu")
        assert "waiting for the running job step" in m.of("live_state", state="waiting_gpu")[0]["message"]
        await asyncio.sleep(0.3)
        assert pids(e.pidfile) == [] and e.lease.owner == "job"       # no worker, step not interrupted
        w = Writer()
        await feed(live, w, sine_pcm(1.0))                            # audio before the grant is not previewed
        assert live.backlog_ms == 0
        job.release()
        await wait_until(lambda: live.state == "listening", what="listening")
        assert e.lease.owner == "live"
        queued = asyncio.create_task(e.lease.acquire("job").wait())   # a job queued meanwhile stays queued
        await asyncio.sleep(0.3)
        assert not queued.done()
        await live.close(1.0)
        await asyncio.wait_for(queued, 5)
        return e
    run(go())


def test_lease_released_on_close_and_on_app_cleanup(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        a, _ = await begin(e, tmp_path)
        await wait_until(lambda: a.state == "listening")
        assert e.lease.owner == "live"
        await a.close(1.0)
        assert e.lease.owner is None
        await a.close(1.0)                                            # idempotent
        b, _ = await begin(e, tmp_path, "rec2")
        await wait_until(lambda: b.state == "listening")
        await e.manager.close_all()
        assert e.lease.owner is None and not any(pid_alive(p) for p in pids(e.pidfile))
    run(go())


def test_close_while_still_waiting_for_the_gpu_cancels_the_wait(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        job = e.lease.acquire("job")
        await job
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: m.of("live_state", state="waiting_gpu"))
        await live.close(1.0)
        job.release()
        await asyncio.sleep(0.1)
        assert e.lease.owner is None and pids(e.pidfile) == []
    run(go())


def test_second_live_session_records_normally_with_a_message(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        a, _ = await begin(e, tmp_path)
        await wait_until(lambda: a.state == "listening")
        b, mb = await begin(e, tmp_path, "rec2")
        await wait_until(lambda: mb.of("live_state"), what="refusal message")
        st = mb.of("live_state")[0]
        assert st["state"] == "off" and "another live session is active" in st["message"]
        b.offer(b"\0\0" * 1600, 0)                                    # harmless
        assert b.preview() is None and a.state == "listening"
        await e.manager.close_all()
    run(go())


# ---- model checks --------------------------------------------------------------------------------------------

def test_large_model_is_refused_without_a_worker_or_a_lease(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch, hub=(TURBO, LARGE), DETAILS_LIVE_STT_MODEL="whisper-large")
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: m.of("live_state"), what="refusal message")
        st = m.of("live_state")[0]
        assert st["state"] == "off" and "whisper-large" in st["message"]
        await asyncio.sleep(0.2)
        assert pids(e.pidfile) == [] and e.lease.owner is None
    run(go())


def test_model_not_installed_says_why(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch, hub=())
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: m.of("live_state"), what="refusal message")
        st = m.of("live_state")[0]
        assert st["state"] == "off" and "not installed" in st["message"]
        assert pids(e.pidfile) == [] and e.lease.owner is None
        live.offer(b"\0\0" * 1600, 0)
    run(go())


# ---- child process ------------------------------------------------------------------------------------------

def test_child_exits_when_the_session_ends(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: live.state == "listening")
        (pid,) = pids(e.pidfile)
        assert pid_alive(pid)
        await live.close(1.0)
        await wait_until(lambda: not pid_alive(pid), 5, "child exit")
    run(go())


def test_commits_get_text_and_frame_counter_offsets(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: live.state == "listening")
        w = Writer()
        await feed(live, w, script_pcm(("s", 2.0), ("t", 1.0), ("s", 0.8)))
        await wait_until(lambda: m.of("live_commit"), what="commit")
        c = m.of("live_commit")[0]
        assert c["idx"] == 0 and 1100 <= c["start_ms"] <= 2000 and c["text"] == f"συζητάμε το θέμα {c['start_ms']}"
        assert live.commits[0]["text"] == c["text"]
        await live.close(1.0)
    run(go())


def test_cold_load_is_not_a_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_FAKE_READY_DELAY", "1.5")
    async def go():
        e = live_env(tmp_path, monkeypatch, DETAILS_LIVE_STT_TIMEOUT_S="1", DETAILS_LIVE_STT_LOAD_TIMEOUT_S="20")
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: live.state == "loading", what="loading")
        await wait_until(lambda: live.state == "listening", 10, "listening after the slow load")
        await feed(live, Writer(), PHRASE)
        await wait_until(lambda: m.of("live_commit"), what="commit after ready")
        assert len(pids(e.pidfile)) == 1                              # never restarted
        await live.close(1.0)
    run(go())


def test_reply_timeout_restarts_once_then_declares_stt_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_FAKE_REPLY_DELAY", "30")
    async def go():
        e = live_env(tmp_path, monkeypatch, DETAILS_LIVE_STT_TIMEOUT_S="0.5")
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: live.state == "listening")
        w = Writer()
        await feed(live, w, PHRASE)
        await wait_until(lambda: m.status("stt", "error"), 20, "stt error")
        assert len(pids(e.pidfile)) == 2
        assert live.state == "failed"
        n = len(w.data)
        await feed(live, w, sine_pcm(1.0))                            # offer after failure: no raise, no effect
        assert len(w.data) == n + 32000
        await live.close(1.0)
        assert not any(pid_alive(p) for p in pids(e.pidfile)) and e.lease.owner is None
    run(go())


def test_child_dies_twice_gives_an_stt_error_and_the_recording_goes_on(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_FAKE_DIE_ON", "1")
    async def go():
        e = live_env(tmp_path, monkeypatch)
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: live.state == "listening")
        await feed(live, Writer(), PHRASE)
        await wait_until(lambda: m.status("stt", "error"), 20, "stt error")
        assert len(pids(e.pidfile)) == 2
        await live.close(1.0)
    run(go())


def test_one_death_is_survived_by_the_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_FAKE_DIE_FIRST_SPAWNS", "1")
    async def go():
        e = live_env(tmp_path, monkeypatch)
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: len(pids(e.pidfile)) == 2 and live.state == "listening", 20, "restart")
        await feed(live, Writer(), PHRASE)
        await wait_until(lambda: m.of("live_commit"), what="commit after restart")
        assert not m.status("stt", "error")
        await live.close(1.0)
    run(go())


def test_partial_is_dropped_while_the_worker_is_busy_and_the_commit_is_not_delayed(tmp_path, monkeypatch):
    delay = 1.5   # > the 0.8 s partial cadence, so a partial is always due while the worker is busy (0.8 was flaky)
    monkeypatch.setenv("LIVE_FAKE_REPLY_DELAY", str(delay))
    async def go():
        e = live_env(tmp_path, monkeypatch)
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: live.state == "listening")
        t0 = time.monotonic()
        await feed(live, Writer(), script_pcm(("t", 1.0), ("s", 0.8), ("t", 4.0)))
        await wait_until(lambda: m.of("live_commit"), 10, "commit")
        took = time.monotonic() - t0
        await wait_until(lambda: live.partials_dropped >= 1, 10, "a dropped partial")
        kinds = [c["kind"] for c in read_calls(e.calls)]
        assert kinds.count("commit") >= 1
        assert took < delay * 3 + 1.0                                 # not stuck behind a queue of partials
        await live.close(1.0)
    run(go())


def test_partials_flow_when_the_worker_is_idle(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: live.state == "listening")
        await feed(live, Writer(), sine_pcm(3.0), pace=0.05)
        await wait_until(lambda: m.of("live_partial"), what="partial")
        await live.close(1.0)
    run(go())


def test_live_off_manager_spawns_nothing_until_begin(tmp_path, monkeypatch):
    e = live_env(tmp_path, monkeypatch)
    assert pids(e.pidfile) == [] and e.lease.owner is None


def test_runner_job_completes_after_live_takes_the_lease_between_lm_load_and_analyse(db, db_url, mk_settings, tmp_path):
    from jobs_helpers import jobs_env, wait_status, wait_until as jwait
    from fakes.engines import FakeStep
    from details.runner.steps import StepRegistry

    async def body():
        log, steps = [], StepRegistry()
        FakeStep(log, "lm_load", sleep=0.3).register(steps)
        FakeStep(log, "analyse", sleep=0.1).register(steps)
        async with jobs_env(db_url, mk_settings, steps=steps) as e:
            jid = await e.svc.enqueue("analyse", None, [{"name": "lm_load", "params": {"model_id": "gemma-cheap"}}, "analyse"])
            await jwait(lambda: ("start", jid, "lm_load") in log, what="lm_load started")
            live = e.runner.lease.acquire("live")          # live asks while lm_load holds the lease
            waiter = asyncio.create_task(live.wait())
            await wait_status(e.svc, jid, "running")
            await asyncio.sleep(0.6)                       # lm_load ended; live is granted before analyse starts
            assert waiter.done() and e.runner.lease.owner == "live"
            assert ("start", jid, "analyse") not in log
            live.release()
            j = await wait_status(e.svc, jid, "done")
            assert [x for x in log if x[0] == "end"] == [("end", jid, "lm_load"), ("end", jid, "analyse")]
            return j
    asyncio.run(asyncio.wait_for(body(), 40))


# ---- record-pause ------------------------------------------------------------------------------------------------

def test_pause_flushes_pending_speech_in_order_and_says_paused(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: live.state == "listening")
        w = Writer()
        await feed(live, w, script_pcm(("t", 2.0)))               # 2 s of speech, not committed yet (no quiet tail)
        live.pause()                                              # synchronously after the last chunk
        await wait_until(lambda: m.of("live_commit"), what="flush commit")
        c = m.of("live_commit")[0]
        assert c["start_ms"] == 0 and c["end_ms"] == 2000         # every chunk queued before the marker is in it
        assert m.of("live_state", message="paused")
        assert live.state == "listening" and live.message == "paused"
        live.continue_()
        await wait_until(lambda: m.of("live_state", message="listening"), what="listening again")
        await live.close(1.0)
    run(go())


def test_timestamps_stay_right_after_a_short_pause_flush(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        live, m = await begin(e, tmp_path)
        await wait_until(lambda: live.state == "listening")
        w = Writer()
        await feed(live, w, script_pcm(("t", 0.2)))               # less than 0.3 s of speech pending
        live.pause()
        await asyncio.sleep(0.2)
        assert m.of("live_commit") == []
        live.continue_()
        await feed(live, w, script_pcm(("t", 1.0), ("s", 0.8)))
        await wait_until(lambda: m.of("live_commit"), what="commit")
        assert m.of("live_commit")[0]["start_ms"] == 200
        await live.close(1.0)
    run(go())


def test_pause_and_continue_are_no_ops_when_live_is_off_or_failed(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        a, _ = await begin(e, tmp_path)
        await wait_until(lambda: a.state == "listening")
        b, mb = await begin(e, tmp_path, "rec2")                  # refused: another live session is active
        await wait_until(lambda: mb.of("live_state"))
        n = len(mb.all)
        b.pause(); b.continue_(); b.pause()
        await asyncio.sleep(0.1)
        assert len(mb.all) == n and b.state == "off" and len(b._q) == 0
        a._fail("boom")
        n = len(a._outbox)
        a.pause(); a.continue_()
        assert len(a._outbox) == n and a.state == "failed" and len(a._q) == 0
        await a.close(1.0)
    run(go())
