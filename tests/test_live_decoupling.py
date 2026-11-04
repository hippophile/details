"""THE invariant: live work never blocks, slows or breaks the WAV write path."""
import asyncio, time

from live_helpers import (FakeClock, Writer, feed, fake_rec, live_env, pid_alive, pids, read_calls, script_pcm,
                          wait_until)
from recording_helpers import SR, blocks, sine_pcm

OFFER_BOUND_S = 0.005       # per spec; the measured value is in tasks.md (4)
LOOP_STALL_BOUND_S = 0.15


async def ticker(stalls, stop):
    last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(0.01)
        now = time.perf_counter()
        stalls.append(now - last - 0.01)
        last = now


def test_offer_is_synchronous_cheap_and_never_awaits(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        live = e.manager.begin(fake_rec(tmp_path), -45.0)
        await wait_until(lambda: live.state == "listening", what="listening")
        w = Writer()
        offers = await feed(live, w, sine_pcm(5.0), pace=0)
        assert not asyncio.iscoroutinefunction(live.offer)
        await live.close(1.0)
        return offers, w
    offers, w = asyncio.run(go())
    assert len(offers) == 50 and max(offers) < OFFER_BOUND_S, max(offers)
    assert bytes(w.data) == sine_pcm(5.0)


def test_blocked_worker_overflow_drops_live_audio_not_wav(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_FAKE_REPLY_DELAY", "5")
    async def go():
        e = live_env(tmp_path, monkeypatch, DETAILS_LIVE_MAX_BACKLOG_S="2", DETAILS_LIVE_PARTIALS="false")
        live = e.manager.begin(fake_rec(tmp_path), -45.0)
        await wait_until(lambda: live.state == "listening", what="listening")
        w, stalls, stop = Writer(), [], asyncio.Event()
        tk = asyncio.create_task(ticker(stalls, stop))
        await feed(live, w, script_pcm(("t", 1.0), ("s", 0.8)))     # one commit: the worker now blocks for 5 s
        await wait_until(lambda: any(c["kind"] == "commit" for c in read_calls(e.calls)), what="commit sent")
        await asyncio.sleep(0.1)                    # the consumer is now parked on the 5 s reply
        n_before, b0 = live.dropped_ms, live.backlog_ms
        pcm = sine_pcm(10.0)
        offers = await feed(live, w, pcm, pace=0)
        stop.set(); await tk
        dropped = live.dropped_ms - n_before
        t0 = time.monotonic()
        await live.close(1.0)
        return w, pcm, offers, stalls, dropped, b0, time.monotonic() - t0
    w, pcm, offers, stalls, dropped, b0, close_s = asyncio.run(go())
    assert bytes(w.data) == script_pcm(("t", 1.0), ("s", 0.8)) + pcm      # the WAV has every frame, byte-identical
    assert dropped == b0 + 10000 - 2000                                   # what was queued + 10 s offered - 2 s that fit
    assert max(offers) < OFFER_BOUND_S and max(w.durations) < 0.05
    assert max(stalls) < LOOP_STALL_BOUND_S, max(stalls)                  # no gap in the event loop while writing
    assert close_s < 1.5


def test_close_with_a_blocked_worker_returns_by_the_deadline_and_kills_the_child(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_FAKE_REPLY_DELAY", "5")
    async def go():
        e = live_env(tmp_path, monkeypatch)
        live = e.manager.begin(fake_rec(tmp_path), -45.0)
        await wait_until(lambda: live.state == "listening", what="listening")
        await feed(live, Writer(), script_pcm(("t", 1.0), ("s", 0.8)))
        await wait_until(lambda: live.busy, what="worker busy")
        t0 = time.monotonic()
        await live.close(1.0)
        return e, time.monotonic() - t0
    e, took = asyncio.run(go())
    assert took < 1.6
    assert not any(pid_alive(p) for p in pids(e.pidfile))
    assert e.lease.owner is None


def test_a_raising_live_never_reaches_the_writer(tmp_path, monkeypatch):
    """The handler wraps offer(); here the session itself swallows an internal failure and marks live failed."""
    async def go():
        e = live_env(tmp_path, monkeypatch)
        live = e.manager.begin(fake_rec(tmp_path), -45.0)
        await wait_until(lambda: live.state == "listening", what="listening")
        monkeypatch.setattr(live, "_enqueue", lambda *a, **k: 1 / 0, raising=False)
        w = Writer()
        await feed(live, w, sine_pcm(1.0))          # must not raise
        await live.close(1.0)
        return live, w
    live, w = asyncio.run(go())
    assert bytes(w.data) == sine_pcm(1.0) and live.failed
