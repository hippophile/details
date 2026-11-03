"""GpuLease: one holder at a time, owners `job` and `live`. `live` is granted ahead of waiting jobs;
a running holder is never interrupted. Usage: `async with lease.acquire("job"): ...`"""
import asyncio
from collections import deque


class _Acquire:
    def __init__(self, lease, owner):
        self.lease, self.owner, self.held = lease, owner, False

    async def wait(self):
        lease = self.lease
        if lease.owner is None and not lease._live and not lease._jobs:
            lease.owner, self.held = self.owner, True
            return self
        fut = asyncio.get_running_loop().create_future()
        queue = lease._live if self.owner == "live" else lease._jobs
        entry = (fut, self.owner)
        queue.append(entry)
        try:
            await fut
        except asyncio.CancelledError:
            if entry in queue:
                queue.remove(entry)
            elif fut.done() and not fut.cancelled():   # granted just as we were cancelled
                lease._grant_next()
            raise
        self.held = True
        return self

    def release(self):
        if self.held:
            self.held = False
            self.lease._grant_next()

    def __await__(self):
        return self.wait().__await__()

    async def __aenter__(self):
        return await self.wait()

    async def __aexit__(self, *exc):
        self.release()


class GpuLease:
    def __init__(self):
        self.owner = None
        self._live, self._jobs = deque(), deque()

    def acquire(self, owner):
        if owner not in ("job", "live"):
            raise ValueError("owner must be 'job' or 'live'")
        return _Acquire(self, owner)

    def _grant_next(self):
        self.owner = None
        for queue in (self._live, self._jobs):
            while queue:
                fut, owner = queue.popleft()
                if not fut.done():
                    self.owner = owner
                    fut.set_result(None)
                    return
