"""Job persistence and lifecycle (the job table, migration 0003). The runner executes; this module owns state."""
import datetime, inspect, logging, uuid

from psycopg.errors import ForeignKeyViolation
from psycopg.types.json import Jsonb

log = logging.getLogger("details.jobs")
TERMINAL = ("done", "failed", "cancelled")
COLUMNS = ("id, recording_id, kind, params, steps, status, step, step_index, step_progress, message, error, "
           "attempt, created_at, started_at, finished_at, heartbeat_at")


class JobError(ValueError):
    """Invalid request (HTTP 422)."""


class NotFound(LookupError):
    pass


class WrongState(RuntimeError):
    pass


def _out(row):
    if row is None:
        return None
    return {k: (str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime.datetime) else v)
            for k, v in row.items()}


def _uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


class JobService:
    def __init__(self, pool, steps, models):
        self.pool, self.steps, self.models = pool, steps, models
        self.wake = None             # set by the runner: called after enqueue/retry
        self.cancel_running = None   # set by the runner: called with a job id after it became `cancelling`

    # ---- requests ---------------------------------------------------------------------------------------

    def _normalize(self, kind, recording_id, steps, params):
        if not isinstance(kind, str) or not kind.strip():
            raise JobError("kind must be a non-empty string")
        if not isinstance(params, dict):
            raise JobError("params must be an object")
        if not isinstance(steps, list) or not steps:
            raise JobError("steps must be a non-empty list")
        if recording_id is not None and _uuid(recording_id) is None:
            raise JobError("recording_id must be a UUID or null")
        out = []
        for s in steps:
            spec = {"name": s, "params": {}} if isinstance(s, str) else s
            if not isinstance(spec, dict) or not isinstance(spec.get("name"), str):
                raise JobError("each step must be a name or {name, params}")
            sp = spec.get("params", {})
            if not isinstance(sp, dict):
                raise JobError(f"step '{spec['name']}': params must be an object")
            if not self.steps.has(spec["name"]):
                raise JobError(f"unknown step '{spec['name']}'")
            if spec["name"] == "lm_load":
                mid = {**params, **sp}.get("model_id")
                if not isinstance(mid, str):
                    raise JobError("step 'lm_load' needs a model_id")
                if mid not in self.models.llm:
                    raise JobError(f"unknown model_id '{mid}'")
            out.append({"name": spec["name"], "params": sp})
        return out

    async def enqueue(self, kind, recording_id, steps, params=None):
        params = {} if params is None else params
        specs = self._normalize(kind, recording_id, steps, params)
        try:
            async with self.pool.connection() as c:
                cur = await c.execute("INSERT INTO job (kind, recording_id, params, steps) VALUES (%s, %s, %s, %s) "
                                      "RETURNING id", (kind, recording_id, Jsonb(params), Jsonb(specs)))
                jid = str((await cur.fetchone())["id"])
        except ForeignKeyViolation:
            raise JobError("recording not found") from None
        if self.wake:
            self.wake()
        return jid

    async def get(self, job_id):
        jid = _uuid(job_id)
        if jid is None:
            return None
        async with self.pool.connection() as c:
            cur = await c.execute(f"SELECT {COLUMNS} FROM job WHERE id=%s", (jid,))
            return _out(await cur.fetchone())

    async def list(self, limit=50):
        async with self.pool.connection() as c:
            cur = await c.execute(f"SELECT {COLUMNS} FROM job ORDER BY created_at DESC, id LIMIT %s", (limit,))
            return [_out(r) for r in await cur.fetchall()]

    async def cancel(self, job_id):
        jid = _uuid(job_id)
        if jid is None:
            raise NotFound(job_id)
        async with self.pool.connection() as c:
            async with c.transaction():
                cur = await c.execute("SELECT status FROM job WHERE id=%s FOR UPDATE", (jid,))
                row = await cur.fetchone()
                if row is None:
                    raise NotFound(job_id)
                if row["status"] == "queued":
                    cur = await c.execute("UPDATE job SET status='cancelled', finished_at=now() WHERE id=%s "
                                          f"RETURNING {COLUMNS}", (jid,))
                elif row["status"] == "running":
                    cur = await c.execute(f"UPDATE job SET status='cancelling' WHERE id=%s RETURNING {COLUMNS}",
                                          (jid,))
                else:
                    raise WrongState(f"job is {row['status']}: only queued or running jobs can be cancelled")
                job = _out(await cur.fetchone())
        if job["status"] == "cancelled":
            await self.run_finalizers(job, "cancelled", None, job["step_index"])
        elif self.cancel_running:
            self.cancel_running(jid)
        return job

    async def retry(self, job_id):
        jid = _uuid(job_id)
        if jid is None:
            raise NotFound(job_id)
        async with self.pool.connection() as c:
            async with c.transaction():
                cur = await c.execute("SELECT status FROM job WHERE id=%s FOR UPDATE", (jid,))
                row = await cur.fetchone()
                if row is None:
                    raise NotFound(job_id)
                if row["status"] != "failed":
                    raise WrongState(f"job is {row['status']}: only failed jobs can be retried")
                cur = await c.execute("UPDATE job SET status='queued', error=NULL, message=NULL, finished_at=NULL "
                                      f"WHERE id=%s RETURNING {COLUMNS}", (jid,))
                job = _out(await cur.fetchone())
        if self.wake:
            self.wake()
        return job

    # ---- runner side ------------------------------------------------------------------------------------

    async def reset_interrupted(self):
        async with self.pool.connection() as c:
            await c.execute("UPDATE job SET status='queued' WHERE status IN ('running', 'cancelling')")

    async def claim_next(self):
        async with self.pool.connection() as c:
            cur = await c.execute(
                "UPDATE job SET status='running', started_at=COALESCE(started_at, now()), attempt=attempt+1, "
                "heartbeat_at=now() WHERE id = (SELECT id FROM job WHERE status='queued' "
                f"ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING {COLUMNS}")
            return _out(await cur.fetchone())

    async def status_of(self, job_id):
        async with self.pool.connection() as c:
            cur = await c.execute("SELECT status FROM job WHERE id=%s", (job_id,))
            row = await cur.fetchone()
            return row["status"] if row else None

    async def begin_step(self, job_id, name, index):
        async with self.pool.connection() as c:
            await c.execute("UPDATE job SET step=%s, step_index=%s, step_progress=0, message=NULL, "
                            "heartbeat_at=now() WHERE id=%s", (name, index, job_id))

    async def update_progress(self, job_id, pct, msg):
        async with self.pool.connection() as c:
            await c.execute("UPDATE job SET step_progress=%s, message=%s, heartbeat_at=now() WHERE id=%s",
                            (pct, msg, job_id))

    async def advance(self, job_id, next_index):
        async with self.pool.connection() as c:
            await c.execute("UPDATE job SET step_index=%s, step_progress=NULL, message=NULL WHERE id=%s",
                            (next_index, job_id))

    async def finish(self, job_id, status, error=None, step=None):
        """-> the finished job, or None when it already left running/cancelling."""
        extra = ", step_progress=1" if status == "done" else ""
        async with self.pool.connection() as c:
            cur = await c.execute(f"UPDATE job SET status=%s, error=%s, finished_at=now(), step=COALESCE(%s, step)"
                                  f"{extra} WHERE id=%s AND status IN ('running', 'cancelling') "
                                  f"RETURNING {COLUMNS}", (status, error, step, job_id))
            return _out(await cur.fetchone())

    async def run_finalizers(self, job, status, error, from_index):
        """Call every on_terminal finalizer of the steps not yet done. Errors are logged, never raised."""
        names = list(dict.fromkeys(s["name"] for s in job["steps"][from_index:]))
        for name in names:
            for fn in self.steps.finalizers(name):
                try:
                    r = fn(job, status, error)
                    if inspect.isawaitable(r):
                        await r
                except Exception:
                    log.exception("finalizer for step %s failed (job %s stays %s)", name, job["id"], status)
