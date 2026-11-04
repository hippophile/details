"""Step registry and context. A step is `async def run(ctx: StepContext) -> dict | None`, registered with
`@steps.register("name")`. Finalizers `@steps.on_terminal("name")` are `(job, status, error)` callables run when a
job ends cancelled or failed. Built-in steps: lm_load (param model_id) and lm_unload."""
import asyncio, pathlib, time
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

    def log(self, level, msg, *, src="step", **data):
        """One line in the job log (levels: debug|info|warn|error). Never put prompt, reply or transcript text here."""
        if self._rt is not None:
            self._rt.log(level, msg, src, data)

    def metrics(self, **kv):
        """Merge model / tokens_in / tokens_out / tok_per_s into this step's record (shown live on the Analyse page)."""
        if self._rt is not None:
            self._rt.metrics(kv)


class StepRegistry:
    def __init__(self):
        self._steps, self._final = {}, {}
        self.register("lm_load")(lm_load)
        self.register("lm_unload")(lm_unload)

    def register(self, name):
        if name.startswith("_"):
            raise ValueError(f"step name '{name}': names starting with '_' are reserved (the job log uses '_job')")

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
    t0 = time.monotonic()
    await ctx.memory.ensure_llm(entry)
    ctx.log("info", "model load requested", src="memory", model=entry.id, seconds=round(time.monotonic() - t0, 2),
            loaded=await _loaded(ctx))


async def lm_unload(ctx):
    t0 = time.monotonic()
    await ctx.memory.release()
    ctx.log("info", "model unload requested", src="memory", seconds=round(time.monotonic() - t0, 2),
            loaded=await _loaded(ctx))


async def _loaded(ctx):
    """Identifiers LM Studio reports after a load/unload (for the log only: never fails the step)."""
    try:
        return [m.identifier for m in await ctx.memory.ctl.list_loaded()]
    except Exception:
        return None


steps = StepRegistry()
