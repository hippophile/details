"""FakeStep (records start/end into a shared log) and a fake child process script.
Run as a script: python engines.py <mode> <seconds>   modes: beat (a line every 0.1 s), silent (no output)."""
import asyncio, os, sys, time


def child_argv(mode="beat", seconds=60):
    return [sys.executable, os.path.abspath(__file__), mode, str(seconds)]


class FakeStep:
    """An async step. fail=N: raise on the first N calls. sleep: seconds to wait. returns: step result."""

    def __init__(self, log, name, sleep=0.0, fail=0, returns=None):
        self.log, self.name, self.sleep, self.fail, self.returns = log, name, sleep, fail, returns
        self.calls = 0

    async def __call__(self, ctx):
        self.calls += 1
        self.log.append(("start", ctx.job_id, self.name))
        ctx.progress(0.5, f"{self.name} half")
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.calls <= self.fail:
            self.log.append(("fail", ctx.job_id, self.name))
            raise RuntimeError(f"{self.name} boom")
        self.log.append(("end", ctx.job_id, self.name))
        return self.returns

    def register(self, registry, name=None):
        registry.register(name or self.name)(self)
        return self


if __name__ == "__main__":
    mode, seconds = sys.argv[1], float(sys.argv[2])
    if mode == "beat":
        print(f"pid {os.getpid()}", flush=True)
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(0.1)
        if mode == "beat":
            print("beat", flush=True)
