"""Trash purge: permanently remove recordings trashed longer than trash.days (startup + every 24 h)."""
import asyncio, logging, re, shutil

from . import recordings

log = logging.getLogger("details.trash")
INTERVAL_S = 24 * 3600
SAFE = re.compile(r"^[A-Za-z0-9_-]+$")


async def purge(pool, settings):
    """-> list of purged recording ids. Skips recordings with a queued/running/cancelling job."""
    async with pool.connection() as c:
        cur = await c.execute("SELECT id FROM recording WHERE deleted_at IS NOT NULL "
                              "AND deleted_at < now() - make_interval(days => %s)", (settings.trash.days,))
        ids = [str(r["id"]) for r in await cur.fetchall()]
        done = []
        for rid in ids:
            cur = await c.execute("SELECT id, status FROM job WHERE recording_id=%s", (rid,))
            jobs = await cur.fetchall()
            if any(j["status"] in ("queued", "running", "cancelling") for j in jobs):
                continue
            job_ids = [str(j["id"]) for j in jobs]
            await c.execute("DELETE FROM recording WHERE id=%s AND deleted_at IS NOT NULL", (rid,))
            shutil.rmtree(recordings.recordings_dir(settings) / rid, ignore_errors=True)
            for j in job_ids:
                if SAFE.match(j):
                    shutil.rmtree(settings.paths.data_dir / "jobs" / j, ignore_errors=True)
            done.append(rid)
    if done:
        log.info("purged %d trashed recording(s)", len(done))
    return done


async def purge_loop(pool, settings):
    while True:
        await asyncio.sleep(INTERVAL_S)
        try:
            await purge(pool, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("trash purge failed (will retry in 24 h)")
