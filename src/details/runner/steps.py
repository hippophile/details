"""Step registry and context. A step is `async def run(ctx: StepContext) -> dict | None`, registered with
`@steps.register("name")`. Finalizers `@steps.on_terminal("name")` are `(job, status, error)` callables run when a
job ends cancelled or failed. Built-in steps: lm_load (param model_id) and lm_unload."""
import asyncio, pathlib
from dataclasses import dataclass, field
from typing import Any


class StepError(RuntimeError):
    pass


@dataclass
class StepContext:
    job_id: str
    step_name: str
    params: dict                    # step params merged over job params
    cancelled: asyncio.Event
    step_dir: pathlib.Path          # <data_dir>/jobs/<job_id>/<step>/ : the ONLY checkpoint dir
    data_dir: pathlib.Path
    settings: Any
    memory: Any                     # MemoryManager
    _rt: Any = field(default=None, repr=False)

    def progress(self, pct, msg=""):
        """pct is a fraction 0..1. Call it plainly or await it; both work. Writes to the DB are throttled."""
        return self._rt.progress(pct, msg)

    async def spawn_child(self, argv, on_line=None):
        """Run a subprocess; each stdout line is a heartbeat. Returns ChildResult(returncode, lines).
        Raises StepError on silence (settings.runner.heartbeat_timeout_s) or a non-zero exit. Cancel kills it."""
        return await self._rt.spawn_child(argv, on_line)


class StepRegistry:
    def __init__(self):
        self._steps, self._final = {}, {}
        self.register("lm_load")(lm_load)
        self.register("lm_unload")(lm_unload)

    def register(self, name):
        def deco(fn):
            self._steps[name] = fn
            return fn
        return deco

    def on_terminal(self, name):
        def deco(fn):
            self._final.setdefault(name, []).append(fn)
            return fn
        return deco

    def has(self, name):
        return name in self._steps

    def get(self, name):
        return self._steps.get(name)

    def finalizers(self, name):
        return list(self._final.get(name, []))


async def lm_load(ctx):
    entry = ctx.memory.registry.llm.get(ctx.params.get("model_id"))
    if entry is None:
        raise StepError(f"unknown model_id {ctx.params.get('model_id')!r}")
    await ctx.memory.ensure_llm(entry)


async def lm_unload(ctx):
    await ctx.memory.release()


steps = StepRegistry()
