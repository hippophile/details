"""Helpers for the live-assistant tests: generated audio, an audio feeder, a fake clock, a manager environment.
Fakes only: a fake worker script (or the real worker with the fake STT engine), FakeLMStudio, no GPU."""
import asyncio, json, math, os, pathlib, struct, sys, time, types

from conftest import make_settings
from details.live.session import LiveManager
from details.llm.lmstudio_ctl import Controller
from details.runner.lease import GpuLease
from details.runner.memory import MemoryManager
from details.services.models import load_registry
from details.settings import REPO_ROOT
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from recording_helpers import SR, blocks
from stt_helpers import SRC, TESTS, TURBO, LARGE, hub_with

FAKE_WORKER = pathlib.Path(__file__).resolve().parent / "fakes" / "live.py"


def tone(seconds, freq=440.0, amp=8000):
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / SR))) for i in range(int(seconds * SR)))


def silence(seconds):
    return b"\0\0" * int(seconds * SR)


def script_pcm(*parts):
    """('t', 3.0) = 3 s of tone, ('s', 0.8) = 0.8 s of silence -> int16 bytes."""
    return b"".join(tone(d) if k == "t" else silence(d) for k, d in parts)


def fake_worker_argv(spec):
    return [sys.executable, str(FAKE_WORKER)]


class FakeClock:
    """sleep() waits until advance() has moved time far enough; nothing runs on wall time."""

    def __init__(self):
        self.t, self.waiters = 0.0, []

    async def sleep(self, s):
        fut = asyncio.get_running_loop().create_future()
        self.waiters.append((self.t + s, fut))
        await fut

    async def advance(self, s):
        self.t += s
        for w in [w for w in self.waiters if w[0] <= self.t + 1e-9]:
            self.waiters.remove(w)
            if not w[1].done():
                w[1].set_result(None)
        await asyncio.sleep(0.05)


class Msgs:
    """The `send` the live session writes to (what the WebSocket would receive)."""

    def __init__(self):
        self.all = []

    async def send(self, m):
        self.all.append(m)

    def of(self, type_, **where):
        return [m for m in self.all if m.get("type") == type_ and all(m.get(k) == v for k, v in where.items())]

    def status(self, part, level=None):
        return [m for m in self.of("live_status", part=part) if level is None or m["level"] == level]


def live_env(tmp_path, monkeypatch, *, worker="fake", hub=(TURBO,), lm=None, mem_free=64.0, clock=None,
             pid_file=True, **env):
    """-> SimpleNamespace(manager, settings, lease, lm, clock, pidfile, calls). worker='fake' = tests/fakes/live.py;
    'real' = details.live.worker with the fake STT engine (needs PYTHONPATH, set here)."""
    monkeypatch.setenv("PYTHONPATH", f"{SRC}:{TESTS}")
    pidfile, calls = tmp_path / "pids.txt", tmp_path / "calls.jsonl"
    if worker == "fake":
        monkeypatch.setenv("LIVE_FAKE_CALLS", str(calls))
        if pid_file:
            monkeypatch.setenv("LIVE_FAKE_PIDFILE", str(pidfile))
    else:
        monkeypatch.setenv("FAKE_STT_OVERRIDE", "1")
        monkeypatch.setenv("FAKE_STT_CALLS", str(calls))
        monkeypatch.setenv("FAKE_STT_PIDFILE", str(pidfile))
        env.setdefault("DETAILS_STT_PLUGINS", "fakes.stt")
    settings = make_settings(tmp_path / "data", None, DETAILS_PATHS_HF_HUB=str(hub_with(tmp_path, *hub)), **env)
    registry = load_registry(REPO_ROOT / "models.toml")
    lm = lm if lm is not None else FakeLMStudio()
    lease = GpuLease()
    memory = MemoryManager(Controller(lm), registry, settings, FakeMemInfo(mem_free))
    clock = clock or FakeClock()
    manager = LiveManager(settings, registry, lease, memory, clock=clock,
                          worker_argv=fake_worker_argv if worker == "fake" else None)
    return types.SimpleNamespace(manager=manager, settings=settings, lease=lease, lm=lm, clock=clock, memory=memory,
                                 pidfile=pidfile, calls=calls, registry=registry)


def fake_rec(tmp_path, name="rec1"):
    """The part of recordings.Session a LiveSession reads."""
    d = tmp_path / "data" / "recordings" / name
    d.mkdir(parents=True, exist_ok=True)
    return types.SimpleNamespace(id=name, dir=d, sample_rate=SR, live=None, title="t")


class Writer:
    """Stands in for WavWriter: records every write call duration and keeps all bytes."""

    def __init__(self):
        self.data, self.frames, self.durations = bytearray(), 0, []

    def write(self, chunk):
        t = time.perf_counter()
        self.data += chunk
        self.frames += len(chunk) // 2
        self.durations.append(time.perf_counter() - t)


async def feed(live, writer, pcm, ms=100, pace=0.0):
    """The WS handler's two lines per block: write first, then offer. -> list of offer durations (seconds)."""
    offers = []
    for b in blocks(pcm, ms):
        fb = writer.frames
        writer.write(b)
        t = time.perf_counter()
        live.offer(b, fb)
        offers.append(time.perf_counter() - t)
        await asyncio.sleep(pace)
    return offers


async def wait_until(pred, timeout=4, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if asyncio.iscoroutine(v):
            v = await v
        if v:
            return v
        await asyncio.sleep(0.03)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def read_calls(path):
    p = pathlib.Path(path)
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False


def pids(path):
    p = pathlib.Path(path)
    return p.read_text().split() if p.exists() else []


# ---- WebSocket-level helpers ------------------------------------------------------------------------------

class WsLog:
    """Reads every server message of one WebSocket in the background, so tests can wait for any of them."""

    def __init__(self, ws):
        self.ws, self.msgs = ws, []
        self.task = asyncio.ensure_future(self._pump())

    async def _pump(self):
        try:
            async for m in self.ws:
                if m.type == 1:
                    self.msgs.append(json.loads(m.data))
        except Exception:
            pass

    def of(self, type_, **where):
        return [m for m in self.msgs if m.get("type") == type_ and all(m.get(k) == v for k, v in where.items())]

    async def wait(self, pred, timeout=20, what="message"):
        return await wait_until(lambda: pred(self), timeout, what)


async def ws_start(c, *, live=True, silence_db=-45, **kw):
    ws = await c.ws_connect("/api/record/ws")
    log = WsLog(ws)
    msg = {"type": "start", "title": "t", "script_id": None, "sample_rate": SR, **kw}
    if live is not None:
        msg["live"] = live
        msg["silence_db"] = silence_db
    await ws.send_json(msg)
    await log.wait(lambda l: l.of("ready"), what="ready")
    return ws, log


async def ws_send(ws, pcm, ms=100, pace=0.0):
    for b in blocks(pcm, ms):
        await ws.send_bytes(b)
        if pace:
            await asyncio.sleep(pace)


def run_ws(mk_settings, db_url, monkeypatch, tmp_path, fn, *, hub=(TURBO,), clock=None, lm=None, mem_free=64.0,
           live_env_vars=True, **env):
    """create_app with the real live worker running the fake STT engine, FakeLMStudio and a fake clock.
    fn(client, settings, extras) -> result."""
    from fakes.lmstudio import FakeLMStudio as _Fake
    from conftest import client_for
    from details.app import create_app
    from stt_helpers import fake_worker_env
    fake_worker_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_STT_PIDFILE", str(tmp_path / "pids.txt"))
    s = mk_settings(db_url, DETAILS_PATHS_HF_HUB=str(hub_with(tmp_path, *hub)), DETAILS_STT_PLUGINS="fakes.stt", **env)
    clock = clock or FakeClock()
    lm = lm if lm is not None else _Fake()

    async def go():
        app = create_app(s, live_clock=clock, lm_transport=lm, meminfo=FakeMemInfo(mem_free))
        async with client_for(app) as c:
            return await fn(c, s, types.SimpleNamespace(clock=clock, lm=lm, app=app, pidfile=tmp_path / "pids.txt",
                                                        calls=tmp_path / "calls.jsonl"))
    return asyncio.run(asyncio.wait_for(go(), 120))
