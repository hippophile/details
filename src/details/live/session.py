"""Live assist: a live transcript (Whisper in a child process) and rolling notes (Gemma E4B) beside a recording.

THE rule: the WAV path comes first and is never touched by anything here. The WebSocket handler writes a frame with
the recording writer and only then calls `LiveSession.offer`, a synchronous O(1) append to a bounded queue that drops
the oldest LIVE audio when full and swallows every exception. Everything else (segmenting, the child process, the
lease wait, LM Studio) runs in tasks of its own and can only ever end live, never the take."""
import asyncio, collections, concurrent.futures, json, logging, os, pathlib, signal, sys, time

import numpy as np
from aiohttp import web

from ..services.models import stt_installed
from ..services.stt_runs import greek_postfix
from .segmenter import Commit, LiveSegmenter, Partial

log = logging.getLogger("details.live")
SRC = pathlib.Path(__file__).resolve().parents[2]          # the `src` folder: the child must find `details`
CLOSE_DEADLINE_S = 2.0
STATE_OFF, STATE_WAIT, STATE_LOAD, STATE_LISTEN, STATE_FAILED = "off", "waiting_gpu", "loading", "listening", "failed"


class SttFailed(Exception):
    pass


class WorkerDied(Exception):
    pass


class RealClock:
    async def sleep(self, s):
        await asyncio.sleep(s)


class WorkerClient:
    """The speech child: persistent, line-JSON requests with a raw int16 payload, one reply per request. A separate
    reader task resolves replies, so a big payload and a reply can never deadlock on pipe buffers. The first failure
    (exit, no reply in `stt_timeout`, no `ready` in `load_timeout`) restarts the child once; the second is SttFailed."""

    def __init__(self, argv, env, stderr_path, stt_timeout, load_timeout):
        self.argv, self.env, self.stderr_path = argv, env, stderr_path
        self.stt_timeout, self.load_timeout = stt_timeout, load_timeout
        self.proc, self.ready, self.failures, self.closing = None, False, 0, False
        self.on_dead = None                    # called with SttFailed when the child dies while idle and cannot restart
        self._pending, self._next, self._lock = {}, 1, asyncio.Lock()
        self._procs, self._tasks = [], set()

    @property
    def busy(self):
        return self._lock.locked()

    def _track(self, task):
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _spawn(self):
        stderr = open(self.stderr_path, "ab")
        # Created in a task of its own and shielded: cancelling asyncio.create_subprocess_exec half way can wait for a
        # process that never exits, and Stop must never wait for that. A child born after a cancel is killed.
        creating = asyncio.ensure_future(asyncio.create_subprocess_exec(
            *self.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=stderr, env=self.env,
            start_new_session=True, limit=1 << 20))
        try:
            proc = await asyncio.shield(creating)
        except asyncio.CancelledError:
            creating.add_done_callback(self._kill_late)
            raise
        finally:
            stderr.close()
        self.proc, self.ready = proc, False
        self._procs.append(proc)
        ready = asyncio.get_running_loop().create_future()
        self._track(asyncio.create_task(self._read(proc, ready)))
        await asyncio.wait_for(ready, self.load_timeout)          # cold model load: its own, longer timeout
        self.ready = True

    async def _read(self, proc, ready):
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                if m.get("ready"):
                    if not ready.done():
                        ready.set_result(True)
                    continue
                fut = self._pending.pop(m.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(m)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("live worker reader failed")
        finally:
            if not ready.done():
                ready.set_exception(WorkerDied("the speech worker exited before it was ready"))
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(WorkerDied("the speech worker exited"))
            self._pending.clear()
            if proc is self.proc and not self.closing and not self._lock.locked():
                self._track(asyncio.create_task(self._revive()))

    async def _revive(self):
        """The child died while idle: restart it now (counts as a failure)."""
        async with self._lock:
            if self.closing or (self.proc is not None and self.proc.returncode is None and self.ready):
                return
            self.failures += 1
            self._kill()
            try:
                await self.ensure()
            except SttFailed as e:
                if self.on_dead:
                    self.on_dead(e)

    @staticmethod
    def _kill_late(fut):
        if not fut.cancelled() and fut.exception() is None:
            try:
                os.killpg(fut.result().pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def _kill(self):
        proc, self.proc, self.ready = self.proc, None, False
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)               # its own session: take the threads' children too
            except (ProcessLookupError, PermissionError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    async def ensure(self):
        """Lock held. Start the child if it is not running and ready."""
        while not (self.proc is not None and self.proc.returncode is None and self.ready):
            if self.closing:
                raise SttFailed("closed")
            if self.failures >= 2:
                raise SttFailed("the speech worker failed twice")
            try:
                await self._spawn()
            except (WorkerDied, asyncio.TimeoutError, OSError) as e:
                log.warning("live worker start failed: %s", type(e).__name__)
                self._kill()
                self.failures += 1

    async def transcribe(self, pcm, start_ms, kind):
        async with self._lock:
            for _ in range(2):
                await self.ensure()
                try:
                    reply = await asyncio.wait_for(self._call(pcm, start_ms, kind), self.stt_timeout)
                except (WorkerDied, asyncio.TimeoutError, OSError) as e:
                    log.warning("live worker request failed: %s", type(e).__name__)
                    self.failures += 1
                    self._kill()
                    continue
                if reply.get("error"):
                    log.warning("live worker: %s", reply["error"])
                    return ""
                return reply.get("text") or ""
            await self.ensure()                                     # raises SttFailed after the second failure
            raise SttFailed("the speech worker failed")

    async def start(self):
        async with self._lock:
            await self.ensure()

    async def _call(self, pcm, start_ms, kind):
        i, self._next = self._next, self._next + 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[i] = fut
        try:
            self.proc.stdin.write(json.dumps({"id": i, "n": len(pcm), "start_ms": start_ms, "kind": kind}).encode()
                                  + b"\n" + pcm)
            await self.proc.stdin.drain()
            return await fut
        finally:
            self._pending.pop(i, None)

    async def close(self):
        self.closing = True
        self._kill()
        for t in list(self._tasks):
            t.cancel()
        procs, self._procs = self._procs, []
        if procs:
            await asyncio.wait([asyncio.ensure_future(p.wait()) for p in procs], timeout=2)


class LiveSession:
    def __init__(self, mgr, rec, silence_db):
        self.mgr, self.rec = mgr, rec
        self.id, self.dir, self.sr = rec.id, pathlib.Path(rec.dir), rec.sample_rate
        self.cfg = mgr.settings.live
        self.silence_db = float(silence_db) if isinstance(silence_db, (int, float)) and not isinstance(silence_db, bool) \
            else -45.0
        self.state, self.message = STATE_OFF, ""
        self.commits, self.dropped_ms, self.partials_dropped = [], 0, 0
        self.notes, self.status = None, {}                      # last live_notes message; last live_status per part
        self.llm_entry, self.llm_loaded = None, False
        self.seg = LiveSegmenter(self.silence_db, self.cfg.partials, self.sr)
        self.worker, self.rolling = None, None
        self.audio_end_ms = 0
        self._q, self._q_ms, self._cap_ms = collections.deque(), 0.0, self.cfg.max_backlog_s * 1000
        self._held, self._accepting, self._closing = b"", False, False
        self._wake = asyncio.Event()
        self._outbox, self._out_evt, self._send, self._sending = collections.deque(maxlen=1000), asyncio.Event(), None, False
        self._pump, self._hold, self._start, self._consumer, self._ptask = None, None, None, None, None
        self._done, self._io = asyncio.Event(), []
        self._closed_started = False

    # ---- state ----------------------------------------------------------------------------------------
    @property
    def failed(self):
        return self.state == STATE_FAILED

    @property
    def busy(self):
        return self.worker is not None and self.worker.busy

    @property
    def backlog_ms(self):
        return int(self._q_ms)

    @property
    def interval_s(self):
        c = self.cfg
        return c.test_interval_s if c.test_mode and c.test_interval_s else c.notes_every_min * 60

    def emit(self, msg):
        self._outbox.append(msg)
        self._out_evt.set()

    def set_state(self, state, message=""):
        self.state, self.message = state, message
        self.emit({"type": "live_state", "state": state, "message": message})

    def set_status(self, part, level, message):
        self.status[part] = {"level": level, "message": message}
        self.emit({"type": "live_status", "part": part, "level": level, "message": message})

    def snapshot(self):
        return {"type": "live_snapshot", "state": self.state, "message": self.message, "lines": list(self.commits),
                "notes": self.notes, "status": dict(self.status)}

    # ---- websocket sink -------------------------------------------------------------------------------
    def attach(self, send, resume=False):
        """send: async callable(dict). On resume the missed messages are dropped and a snapshot replaces them."""
        self._send = send
        if resume:
            self._outbox.clear()
            self.emit(self.snapshot())
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._pump_out())
        self._out_evt.set()

    def detach(self):
        self._send = None

    async def _pump_out(self):
        while True:
            await self._out_evt.wait()
            self._out_evt.clear()
            while self._outbox and self._send is not None:
                m = self._outbox.popleft()
                self._sending = True
                try:
                    await self._send(m)
                except Exception:
                    self._send = None                              # the socket is gone; a resume brings a snapshot
                finally:
                    self._sending = False

    async def _flush_out(self, timeout):
        end = time.monotonic() + timeout
        while (self._outbox or self._sending) and self._send is not None and time.monotonic() < end:
            await asyncio.sleep(0.01)

    # ---- start ----------------------------------------------------------------------------------------
    def refuse(self, message):
        self.set_state(STATE_OFF, message)

    def begin(self):
        """Checks, then the start task. Never raises."""
        mgr, cfg = self.mgr, self.cfg
        try:
            if mgr.active is not None:
                return self.refuse("another live session is active; this take is recording without live assist")
            entry = mgr.registry.stt.get(cfg.stt_model)
            if entry is None or entry.role != "fast":
                return self.refuse(f"live speech-to-text refused: {cfg.stt_model} is not a fast-role speech model "
                                   "(live.stt_model); live uses only the fast Whisper model. Recording continues")
            if not entry.enabled or not stt_installed(mgr.settings.paths.hf_hub, entry.repo):
                return self.refuse(f"{entry.label} is not installed (weights for {entry.repo} are not in the "
                                   "Hugging Face cache), so live text is unavailable. Recording continues")
            self.stt_entry = entry
            llm = mgr.registry.llm.get(cfg.llm_model)
            if llm is not None and llm.role == "cheap":
                self.llm_entry = llm
            else:
                self.set_status("notes", "warn", f"Live notes are off: {cfg.llm_model} is not a cheap-role model "
                                "(live.llm_model); the big model is never used while recording")
            mgr.active = self
            mgr.sessions.add(self)
            self._start = asyncio.create_task(self._run_start())
        except Exception as e:
            log.exception("live begin failed")
            self._accepting = False
            self.set_state(STATE_FAILED, f"live could not start ({type(e).__name__}); recording continues")

    async def _run_start(self):
        mgr = self.mgr
        try:
            if mgr.lease.owner is not None:
                self.set_state(STATE_WAIT, "waiting for the running job step (recording continues; live text starts "
                                           "when it finishes)")
            self._hold = mgr.lease.acquire("live")
            await self._hold
            self._accepting = True                                  # audio before the grant is not previewed
            self.set_state(STATE_LOAD, "loading the speech model (a cold start can take a minute)")
            self.worker = self._make_worker()
            self.worker.on_dead = self._stt_failed
            from . import rolling as rolling_mod
            prewarm = asyncio.ensure_future(self._off(rolling_mod.prewarm))
            await self.worker.start()
            await asyncio.gather(prewarm, return_exceptions=True)
            self.set_state(STATE_LISTEN, "listening")
            self._consumer = asyncio.create_task(self._consume())
            if self.llm_entry is not None:
                self.rolling = rolling_mod.Rolling(self)
                self.rolling.start()
        except asyncio.CancelledError:
            raise
        except SttFailed as e:
            self._stt_failed(e)
        except Exception as e:
            log.exception("live start failed")
            self._fail(f"live could not start ({type(e).__name__}); recording continues")

    def _make_worker(self):
        mgr, cfg = self.mgr, self.cfg
        e = self.stt_entry
        spec = {"engine": e.engine, "model": {"repo": e.repo, **e.options}, "language": cfg.language,
                "initial_prompt": mgr.settings.stt.vocab_hint if cfg.vocab_hint else None,
                "plugins": [m.strip() for m in mgr.settings.stt.plugins.split(",") if m.strip()]}
        argv = mgr.worker_argv(spec) if mgr.worker_argv else [sys.executable, "-m", "details.live.worker",
                                                                 json.dumps(spec, ensure_ascii=False)]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        d = self.dir / "live"
        d.mkdir(parents=True, exist_ok=True)
        return WorkerClient(argv, env, d / "worker.stderr.log", cfg.stt_timeout_s, cfg.stt_load_timeout_s)

    async def _off(self, fn, *a):
        """Run blocking work on the live thread, never on the writer's executor."""
        return await asyncio.get_running_loop().run_in_executor(self.mgr.io, fn, *a)

    # ---- the one call the WebSocket handler makes -----------------------------------------------------
    def offer(self, data, frames_before):
        """Synchronous, O(1), never awaits, never raises. `frames_before` = the writer's frame count before it wrote
        `data` (so times match the saved file). When the queue is full the oldest live audio is dropped and counted."""
        try:
            if self._accepting:
                self._enqueue(data, frames_before)
        except Exception as e:
            self._offer_failed(e)

    def _enqueue(self, data, frames_before):
        chunk = self._held + bytes(data)
        if len(chunk) % 2:
            chunk, self._held = chunk[:-1], chunk[-1:]
        else:
            self._held = b""
        if not chunk:
            return
        sr, frame = self.sr, frames_before
        ms = len(chunk) / 2 * 1000 / sr
        self.audio_end_ms = int((frame + len(chunk) // 2) * 1000 / sr)
        if ms > self._cap_ms:                                       # a burst bigger than the whole queue
            keep = int(self._cap_ms * sr / 1000) * 2
            self.dropped_ms += int((len(chunk) - keep) / 2 * 1000 / sr)
            frame, chunk, ms = frame + (len(chunk) - keep) // 2, chunk[-keep:], keep / 2 * 1000 / sr
        while self._q and self._q_ms + ms > self._cap_ms:
            old = self._q.popleft()
            d = len(old[1]) / 2 * 1000 / sr
            self._q_ms -= d
            self.dropped_ms += d
        self._q.append((frame, chunk))
        self._q_ms += ms
        self._wake.set()

    def mark_failed(self, e):
        """For the WebSocket handler's own try/except around `offer`."""
        self._offer_failed(e)

    def _offer_failed(self, e):
        try:
            log.error("live offer failed (%s); live is off, the recording is unaffected", type(e).__name__)
            self._fail("Live text stopped because of an internal error. Recording continues")
        except Exception:
            pass

    def _fail(self, message):
        self._accepting = False
        self._q.clear()
        self._q_ms = 0.0
        self.set_state(STATE_FAILED, message)
        if self.rolling is not None:
            self.rolling.stop()

    def _stt_failed(self, e):
        self.set_status("stt", "error", "Live speech-to-text stopped: the speech worker failed twice. "
                                        "Recording continues")
        self._fail("Live speech-to-text stopped. Recording continues")

    # ---- consumer -------------------------------------------------------------------------------------
    async def _consume(self):
        try:
            while True:
                if not self._q:
                    if self._closing:
                        break
                    self._wake.clear()
                    await self._wake.wait()
                    continue
                frame, chunk = self._q.popleft()
                self._q_ms -= len(chunk) / 2 * 1000 / self.sr
                for ev in self.seg.feed(chunk, frame):
                    await self._handle(ev)
                await asyncio.sleep(0)
            for ev in self.seg.flush():
                await self._handle(ev)
        except asyncio.CancelledError:
            raise
        except SttFailed as e:
            self._stt_failed(e)
        except Exception as e:
            log.exception("live consumer failed")
            self._fail(f"Live text stopped ({type(e).__name__}). Recording continues")

    @staticmethod
    def _pcm(audio):
        return (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()

    async def _handle(self, ev):
        if isinstance(ev, Commit):
            text = (await self.worker.transcribe(self._pcm(ev.audio), ev.start_ms, "commit")).strip()
            if text and self.cfg.language == "el":
                text = greek_postfix(text)                          # same rule as the full STT step
            if text:
                line = {"idx": len(self.commits), "start_ms": ev.start_ms, "end_ms": ev.end_ms, "text": text}
                self.commits.append(line)
                self.emit({"type": "live_commit", **line})
                self._persist("transcript.jsonl", json.dumps(line, ensure_ascii=False) + "\n", append=True)
        elif isinstance(ev, Partial):
            if self.worker.busy or (self._ptask is not None and not self._ptask.done()):
                self.partials_dropped += 1                        # commits have priority; a busy worker skips partials
                return
            self._ptask = asyncio.create_task(self._partial(ev, len(self.commits)))

    async def _partial(self, ev, n_commits):
        try:
            text = (await self.worker.transcribe(self._pcm(ev.audio), ev.start_ms, "partial")).strip()
            if text and len(self.commits) == n_commits and not self._closing:
                self.emit({"type": "live_partial", "text": text})
        except asyncio.CancelledError:
            raise
        except SttFailed as e:
            self._stt_failed(e)
        except Exception:
            log.exception("live partial failed")

    # ---- files kept next to the recording -------------------------------------------------------------
    def _persist(self, name, text, append=False):
        d = self.dir / "live"

        def write():
            d.mkdir(parents=True, exist_ok=True)
            if append:
                with open(d / name, "a", encoding="utf-8") as f:
                    f.write(text)
            else:
                tmp = d / (name + ".tmp")
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, d / name)
        fut = asyncio.get_running_loop().run_in_executor(self.mgr.io, write)
        self._io.append(fut)
        fut.add_done_callback(lambda f: (self._io.remove(f) if f in self._io else None,
                                         f.exception() and log.warning("live file write failed: %s", f.exception())))

    # ---- finalize support -----------------------------------------------------------------------------
    def preview(self):
        """What the saved stt_run is built from (commits received so far), or None when there is nothing."""
        if not self.commits:
            return None
        e = getattr(self, "stt_entry", None)
        return {"lines": list(self.commits), "dropped_ms": int(self.dropped_ms), "silence_db": self.silence_db,
                "model_id": self.cfg.stt_model, "engine": e.engine if e else "mlx_whisper",
                "language": self.cfg.language}

    # ---- end ------------------------------------------------------------------------------------------
    async def close(self, deadline=None):
        """Idempotent. Never waits longer than ~`deadline` seconds: what the worker has not answered by then is lost
        (the take is not). Releases the GPU lease, kills the child, unloads the notes model."""
        if self._closed_started:
            await self._done.wait()
            return
        self._closed_started = True
        deadline = self.mgr.close_deadline_s if deadline is None else deadline
        t_end = time.monotonic() + deadline
        try:
            await self._close(t_end)
        except Exception:
            log.exception("live close failed")
        finally:
            self.mgr.active = None if self.mgr.active is self else self.mgr.active
            self.mgr.sessions.discard(self)
            self._done.set()

    @staticmethod
    async def _cancel(task, timeout=1.0):
        """Cancel and wait a little; never wait for a task that does not stop."""
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=timeout)

    async def _close(self, t_end):
        self._accepting, self._closing = False, True
        self._wake.set()
        await self._cancel(self._start)
        if self.rolling is not None:
            await self.rolling.close()
        if self._consumer is not None and not self._consumer.done():       # drain what is queued, within the budget
            await asyncio.wait({self._consumer}, timeout=max(0.0, t_end - time.monotonic() - 0.5))
            await self._cancel(self._consumer)
        await self._cancel(self._ptask)
        if self.worker is not None:
            await self.worker.close()
        if self._hold is not None:
            self._hold.release()
        if self.llm_loaded:
            task = asyncio.create_task(self.mgr.release_model())
            self.mgr.track(task)
            await asyncio.wait({task}, timeout=max(0.2, t_end - time.monotonic()))
        if self.state != STATE_FAILED:
            self.state = STATE_OFF
        await self._flush_out(max(0.1, min(0.5, t_end - time.monotonic())))
        if self._io:
            await asyncio.wait(list(self._io), timeout=1.0)
        if self._pump is not None:
            self._pump.cancel()


class LiveManager:
    """One per app. Holds the shared GpuLease (created in create_app, passed in; never created here)."""

    def __init__(self, settings, registry, lease, memory, *, clock=None, worker_argv=None,
                 close_deadline_s=CLOSE_DEADLINE_S):
        self.settings, self.registry, self.lease, self.memory = settings, registry, lease, memory
        self.clock = clock or RealClock()
        self.worker_argv, self.close_deadline_s = worker_argv, close_deadline_s
        self.io = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="live-io")   # not the writer's executor
        self.active, self.sessions, self._bg = None, set(), set()

    def begin(self, rec, silence_db=None):
        s = LiveSession(self, rec, silence_db)
        s.begin()
        return s

    def track(self, task):
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def release_model(self):
        from ..llm.lmstudio_ctl import LMStudioError
        try:
            await self.memory.release()
        except LMStudioError as e:
            log.warning("live: could not unload the notes model: %s", e)
        except Exception:
            log.exception("live: unload failed")

    async def close_all(self):
        for s in list(self.sessions):
            await s.close()
        if self._bg:
            await asyncio.wait(list(self._bg), timeout=5)


LIVE = web.AppKey("live", LiveManager)
