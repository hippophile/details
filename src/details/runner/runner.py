"""JobRunner: one asyncio worker, FIFO, one step at a time under the GpuLease.
A step's checkpoint is <data_dir>/jobs/<job_id>/<step>/done.json (written atomically, then step_index advances).
Restart: running/cancelling jobs go back to queued and finished steps are skipped. No automatic retry."""
import asyncio, json, logging, os, time

from .child import ChildGroup, run_child
from .lease import GpuLease
from .steps import StepContext, StepError

log = logging.getLogger("details.runner")


class _Current:
    """What the worker is doing right now (one job)."""

    def __init__(self, job_id):
        self.job_id = job_id
        self.cancelled = asyncio.Event()
        self.children = ChildGroup()
        self.pct, self.msg, self.dirty, self.last_write = None, None, False, 0.0


class _Runtime:
    """Per-step helpers behind StepContext.progress / spawn_child."""

    def __init__(self, runner, cur, ctx_dir):
        self.runner, self.cur, self.dir = runner, cur, ctx_dir
        self.cancelled = cur.cancelled

    def progress(self, pct, msg):
        self.cur.pct, self.cur.msg, self.cur.dirty = max(0.0, min(1.0, float(pct))), msg, True
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(None)
        return fut

    async def spawn_child(self, argv, on_line):
        def line(text):
            self.cur.dirty = True          # a heartbeat: refreshes heartbeat_at on the next throttled write
            if on_line:
                on_line(text)
        rs = self.runner.settings.runner
        return await run_child(argv, heartbeat_timeout=rs.heartbeat_timeout_s, grace=rs.cancel_grace_s,
                              cancelled=self.cur.cancelled, group=self.cur.children, on_line=line,
                              stderr_path=self.dir / "child.stderr.log")


class JobRunner:
    def __init__(self, service, settings, memory, steps, lease=None, progress_interval=1.0, poll_s=0.5):
        self.svc, self.settings, self.memory, self.steps = service, settings, memory, steps
        self.lease = lease or GpuLease()
        self.progress_interval, self.poll_s = progress_interval, poll_s
        self.current = None
        self._task = None
        self._wake = asyncio.Event()

    # ---- lifecycle --------------------------------------------------------------------------------------

    async def start(self):
        await self.svc.reset_interrupted()
        self.svc.wake, self.svc.cancel_running = self.wake, self._cancel_running
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self.svc.wake = self.svc.cancel_running = None

    def wake(self):
        self._wake.set()

    def _cancel_running(self, job_id):
        if self.current and self.current.job_id == job_id:
            self.current.cancelled.set()

    # ---- loop -------------------------------------------------------------------------------------------

    async def _loop(self):
        while True:
            try:
                job = await self.svc.claim_next()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("claiming a job failed; retrying")
                job = None
            if job is None:
                try:
                    await asyncio.wait_for(self._wake.wait(), self.poll_s)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                continue
            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("job %s crashed the runner step loop", job["id"])
                await self._end(job, job["step_index"], "failed", "internal error: see server log")
            finally:
                self.current = None

    async def _run_job(self, job):
        jid, specs = job["id"], job["steps"]
        cur = self.current = _Current(jid)
        ticker = asyncio.create_task(self._ticker(cur))
        try:
            for i in range(job["step_index"], len(specs)):
                name = specs[i]["name"]
                if await self._cancelling(cur):
                    return await self._end(job, i, "cancelled")
                step_dir = self.settings.paths.data_dir / "jobs" / jid / name
                if self._marker_ok(step_dir, i):
                    await self.svc.advance(jid, i + 1)
                    continue
                fn = self.steps.get(name)
                if fn is None:
                    return await self._end(job, i, "failed", f"{name}: unknown step '{name}'")
                step_dir.mkdir(parents=True, exist_ok=True)
                await self.svc.begin_step(jid, name, i)
                cur.pct, cur.msg, cur.dirty, cur.last_write = 0.0, None, False, 0.0
                ctx = StepContext(job_id=jid, step_name=name, params={**job["params"], **specs[i]["params"]},
                                  cancelled=cur.cancelled, step_dir=step_dir, data_dir=self.settings.paths.data_dir,
                                  settings=self.settings, memory=self.memory, _rt=_Runtime(self, cur, step_dir))
                try:
                    result = await self._run_step(cur, fn, ctx)
                except StepError as e:
                    return await self._end(job, i, "failed", f"{name}: {e}")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.exception("step %s of job %s failed", name, jid)
                    return await self._end(job, i, "failed", f"{name}: {e or type(e).__name__}")
                if cur.cancelled.is_set() or await self._cancelling(cur):
                    return await self._end(job, i, "cancelled")
                await self._flush(cur)
                self._write_marker(step_dir, i, name, result)
                await self.svc.advance(jid, i + 1)
            await self.svc.finish(jid, "done")
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)

    async def _run_step(self, cur, fn, ctx):
        grace = self.settings.runner.cancel_grace_s
        hold = self.lease.acquire("job")
        waiter = asyncio.create_task(hold.wait())
        cancel_wait = asyncio.create_task(cur.cancelled.wait())
        try:
            await asyncio.wait({waiter, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
            if not waiter.done():            # cancelled while waiting for the GPU
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
                hold.release()
                return None
            task = asyncio.create_task(fn(ctx))
            try:
                await asyncio.wait({task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
                if not task.done():          # cancel: kill children, give the step `grace` to wind down, then stop it
                    await cur.children.kill_all(grace)
                    await asyncio.wait({task}, timeout=grace)
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return None
                return task.result()
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await cur.children.kill_all(0)
        finally:
            cancel_wait.cancel()
            waiter.cancel()
            hold.release()

    # ---- helpers ----------------------------------------------------------------------------------------

    async def _cancelling(self, cur):
        if cur.cancelled.is_set():
            return True
        if await self.svc.status_of(cur.job_id) == "cancelling":
            cur.cancelled.set()
            return True
        return False

    async def _end(self, job, index, status, error=None):
        step = job["steps"][index]["name"] if index < len(job["steps"]) else None
        done = await self.svc.finish(job["id"], status, error, step if status == "failed" else None)
        if done is None:
            return
        await self.svc.run_finalizers(done, status, error, index)

    @staticmethod
    def _marker_ok(step_dir, index):
        try:
            data = json.loads((step_dir / "done.json").read_text())
        except (OSError, ValueError):
            return False
        return not isinstance(data, dict) or data.get("index", index) == index

    @staticmethod
    def _write_marker(step_dir, index, name, result):
        tmp = step_dir / "done.json.tmp"
        tmp.write_text(json.dumps({"index": index, "step": name, "result": result}, default=str))
        os.replace(tmp, step_dir / "done.json")

    async def _flush(self, cur):
        if cur.dirty:
            cur.dirty, cur.last_write = False, time.monotonic()
            await self.svc.update_progress(cur.job_id, cur.pct, cur.msg)

    async def _ticker(self, cur):
        """Throttled progress/heartbeat writes: at most one per progress_interval per job."""
        while True:
            await asyncio.sleep(min(self.progress_interval, 0.1))
            if cur.dirty and time.monotonic() - cur.last_write >= self.progress_interval:
                try:
                    await self._flush(cur)
                except Exception:
                    log.exception("progress write failed")
