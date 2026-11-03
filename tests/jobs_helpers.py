"""Harness for job tests: pool + fake LM Studio + JobService + JobRunner with short timeouts. Fakes only."""
import asyncio, contextlib, types

from details.db import open_pool
from details.llm.lmstudio_ctl import Controller
from details.runner.memory import MemoryManager
from details.runner.runner import JobRunner
from details.runner.steps import StepRegistry
from details.services.jobs import JobService
from details.services.models import load_registry
from details.settings import REPO_ROOT
from fakes.lmstudio import FakeLMStudio, FakeMemInfo

TIMEOUT = 10


async def wait_until(pred, timeout=TIMEOUT, what="condition"):
    async def loop():
        while True:
            v = pred()
            if asyncio.iscoroutine(v):
                v = await v
            if v:
                return v
            await asyncio.sleep(0.03)
    try:
        return await asyncio.wait_for(loop(), timeout)
    except asyncio.TimeoutError:
        raise AssertionError(f"timed out after {timeout}s waiting for {what}") from None


async def wait_status(svc, jid, want, timeout=TIMEOUT):
    want = {want} if isinstance(want, str) else set(want)
    last = {}

    async def check():
        last.update(await svc.get(jid))
        return last["status"] in want
    try:
        await wait_until(check, timeout, f"job {want}")
    except AssertionError:
        raise AssertionError(f"job stayed {last.get('status')!r} (step={last.get('step')!r}, "
                             f"error={last.get('error')!r}), wanted {want}") from None
    return last


@contextlib.asynccontextmanager
async def jobs_env(db_url, mk_settings, *, steps=None, lm=None, start=True, grace="1", heartbeat="120", **env):
    settings = mk_settings(db_url, DETAILS_RUNNER_CANCEL_GRACE_S=grace, DETAILS_RUNNER_HEARTBEAT_TIMEOUT_S=heartbeat,
                           **env)
    settings.paths.data_dir.mkdir(parents=True, exist_ok=True)
    pool = await open_pool(db_url)
    models = load_registry(REPO_ROOT / "models.toml")
    steps = steps if steps is not None else StepRegistry()
    lm = lm if lm is not None else FakeLMStudio()
    memory = MemoryManager(Controller(lm), models, settings, FakeMemInfo())
    svc = JobService(pool, steps, models)
    runner = JobRunner(svc, settings, memory, steps, progress_interval=0.05)
    e = types.SimpleNamespace(settings=settings, pool=pool, models=models, steps=steps, lm=lm, memory=memory,
                              svc=svc, runner=runner, data_dir=settings.paths.data_dir)
    if start:
        await runner.start()
    try:
        yield e
    finally:
        await runner.stop()
        await pool.close()


def run(coro_fn, timeout=40):
    """asyncio.run with a hard ceiling so a broken test cannot hang the suite."""
    asyncio.run(asyncio.wait_for(coro_fn(), timeout))
