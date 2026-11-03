import asyncio

from details.runner.lease import GpuLease


def run(coro):
    asyncio.run(asyncio.wait_for(coro, 10))


def test_live_granted_before_waiting_job_and_holder_not_interrupted():
    async def go():
        lease, order = GpuLease(), []

        async def worker(owner, name, hold=0.0):
            async with lease.acquire(owner):
                order.append(f"{name}+")
                await asyncio.sleep(hold)
                order.append(f"{name}-")

        first = asyncio.create_task(worker("job", "j1", hold=0.2))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(worker("job", "j2"))     # waits first
        await asyncio.sleep(0.02)
        live = asyncio.create_task(worker("live", "live"))    # waits later, wins
        await asyncio.gather(first, second, live)
        assert order == ["j1+", "j1-", "live+", "live-", "j2+", "j2-"]
    run(go())


def test_uncontended_and_reusable():
    async def go():
        lease = GpuLease()
        for owner in ("job", "live", "job"):
            async with lease.acquire(owner):
                assert lease.owner == owner
        assert lease.owner is None
    run(go())


def test_cancelled_waiter_leaves_queue():
    async def go():
        lease = GpuLease()
        async with lease.acquire("job"):
            t = asyncio.create_task(lease.acquire("job").__aenter__())
            await asyncio.sleep(0.02)
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        async with lease.acquire("job"):     # not stuck behind the cancelled waiter
            pass
    run(go())
