"""In-memory live view of the LM Studio reply that is being written, per running job.
Holds the text of only the CURRENT attempt (capped at `cap` characters, the tail is kept) plus a bounded ring of recent
events (`max_events`, each with an increasing id) so a reconnecting page can resume. Nothing is persisted: the validated result in analysis_run/output is the record.
Producers (the analyse step) call start_attempt/delta/end_attempt/fallback/end_job; consumers (the SSE route)
read snapshot()/events_since() and block in wait(). Delta text is never logged."""
import asyncio, collections

CAP = 256 * 1024
MAX_EVENTS = 4096


class _Job:
    def __init__(self, max_events=MAX_EVENTS):
        self.seq = 0
        self.kind = self.chunk = self.valid = None
        self.attempt = self.pieces = 0
        self.text, self.truncated, self.active = "", False, False
        self.events = collections.deque(maxlen=max_events)    # (seq, type, data), oldest dropped first
        self.floor = 0                                          # events with seq <= floor can no longer be resumed
        self.subs = 0
        self.changed = asyncio.Event()

    def push(self, typ, data):
        self.seq += 1
        if len(self.events) == self.events.maxlen:
            self.floor = self.events[0][0]
        self.events.append((self.seq, typ, data))
        self.wake()
        return self.seq

    def wake(self):
        self.changed.set()
        self.changed = asyncio.Event()


class StreamHub:
    def __init__(self, cap=CAP, max_events=MAX_EVENTS):
        self.cap, self.max_events = cap, max_events
        self._jobs = {}

    def _get(self, job_id):
        j = self._jobs.get(str(job_id))
        if j is None:
            j = self._jobs[str(job_id)] = _Job(self.max_events)
        return j

    # ---- producer side -----------------------------------------------------------------------------------
    def start_attempt(self, job_id, kind, attempt, chunk=None):
        j = self._get(job_id)
        j.kind, j.attempt, j.chunk = kind, attempt, list(chunk) if chunk else None
        j.text, j.truncated, j.pieces, j.valid, j.active = "", False, 0, None, True
        j.push("attempt_start", {"kind": kind, "attempt": attempt, "chunk": j.chunk})

    def delta(self, job_id, text, n=1):
        j = self._get(job_id)
        j.text += text
        if len(j.text) > self.cap:
            j.text, j.truncated = j.text[-self.cap:], True
        j.pieces += n
        j.push("delta", {"text": text, "n": n})

    def end_attempt(self, job_id, **kv):
        j = self._get(job_id)
        j.active, j.valid = False, kv.get("valid")
        j.push("attempt_end", dict(kv))

    def fallback(self, job_id, reason):
        self._get(job_id).push("fallback", {"reason": reason})

    def end_job(self, job_id):
        """The job's analyse step is over: drop the text and wake every waiter."""
        j = self._jobs.get(str(job_id))
        if j is None:
            return
        j.text, j.truncated, j.pieces, j.active, j.kind, j.chunk, j.valid, j.attempt = "", False, 0, False, None, None, None, 0
        j.events.clear()
        j.floor = j.seq
        j.push("idle", {})                                      # tells the page to clear its box
        if j.subs <= 0:
            self._jobs.pop(str(job_id), None)

    # ---- consumer side -----------------------------------------------------------------------------------
    def snapshot(self, job_id):
        j = self._jobs.get(str(job_id))
        if j is None:
            return {"seq": 0, "kind": None, "attempt": 0, "chunk": None, "text": "", "pieces": 0, "truncated": False,
                    "active": False, "valid": None}
        return {"seq": j.seq, "kind": j.kind, "attempt": j.attempt, "chunk": j.chunk, "text": j.text,
                "pieces": j.pieces, "truncated": j.truncated, "active": j.active, "valid": j.valid}

    def events_since(self, job_id, seq):
        """-> [(seq, type, data)] newer than `seq`, or None when `seq` is older than the buffer (send a snapshot)."""
        j = self._jobs.get(str(job_id))
        if j is None:
            return [] if seq == 0 else None
        if seq < j.floor:
            return None
        return [e for e in j.events if e[0] > seq]

    async def wait(self, job_id, seq, timeout):
        """True when the job has something newer than `seq`, False after `timeout` seconds."""
        j = self._get(job_id)
        if j.seq > seq:
            return True
        try:
            await asyncio.wait_for(j.changed.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    def subscribe(self, job_id):
        self._get(job_id).subs += 1

    def release(self, job_id):
        j = self._jobs.get(str(job_id))
        if j is None:
            return
        j.subs = max(0, j.subs - 1)
        if j.subs == 0 and not j.active and not j.events:
            self._jobs.pop(str(job_id), None)
