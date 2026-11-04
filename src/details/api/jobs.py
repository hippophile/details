"""Jobs API: POST/GET /api/jobs, GET /api/jobs/{id}, POST cancel|retry, SSE /api/jobs/{id}/events and /stream
(the live LM Studio reply of the analyse step, from the in-memory StreamHub)."""
import asyncio, json, time

from aiohttp import web

from ..llm.stream_hub import StreamHub
from ..runner import stepinfo
from ..runner.joblog import secrets_from
from ..services.jobs import TERMINAL, JobError, JobService, NotFound, WrongState

JOBS = web.AppKey("jobs", JobService)
STREAM_HUB = web.AppKey("stream_hub", StreamHub)
SSE_POLL_S = 0.25
SSE_PING_S = 15
STREAM_COALESCE_S = 0.1       # at most one write per this long on /stream
STREAM_STATUS_S = 0.5         # how often /stream re-reads the job status


def job_steps(request, job):
    """The `steps` list of a job (step.json records; settings and data_dir come from the app keys)."""
    from ..app import SETTINGS          # late: app.py imports this module
    settings = request.app[SETTINGS]
    return stepinfo.view(settings.paths.data_dir, job, secrets_from(settings))


def add_jobs_routes(app):
    def svc(request):
        s = request.app.get(JOBS)
        if s is None:
            raise web.HTTPServiceUnavailable(text=json.dumps({"error": "database not configured"}),
                                             content_type="application/json")
        return s

    def err(msg, status):
        return web.json_response({"error": msg}, status=status)

    async def create(request):
        s = svc(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return err("expected a JSON object", 422)
        try:
            jid = await s.enqueue(body.get("kind"), body.get("recording_id"), body.get("steps"),
                                  body.get("params") if body.get("params") is not None else {})
        except JobError as e:
            return err(str(e), 422)
        return web.json_response(await s.get(jid), status=201)

    async def list_(request):
        return web.json_response(await svc(request).list())

    async def get_(request):
        j = await svc(request).get(request.match_info["id"])
        return web.json_response(j) if j else err("job not found", 404)

    def action(name):
        async def handler(request):
            try:
                return web.json_response(await getattr(svc(request), name)(request.match_info["id"]))
            except NotFound:
                return err("job not found", 404)
            except WrongState as e:
                return err(str(e), 409)
        return handler

    def state(request, job, seq):
        return {"seq": seq, "job": job["id"], "status": job["status"], "step": job["step"],
                "pct": job["step_progress"], "msg": job["message"], "steps": job_steps(request, job)}

    async def events(request):
        s = svc(request)
        job = await s.get(request.match_info["id"])
        if job is None:
            return err("job not found", 404)
        try:
            seq = max(0, int(request.headers.get("Last-Event-ID", "0")))
        except ValueError:
            seq = 0
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})
        await resp.prepare(request)
        prev, quiet = None, 0.0
        try:
            while job is not None:
                cur = state(request, job, 0)
                if cur != prev:
                    seq, prev, quiet = seq + 1, cur, 0.0
                    await resp.write(f"id: {seq}\ndata: {json.dumps({**cur, 'seq': seq})}\n\n".encode())
                if job["status"] in TERMINAL:
                    break
                await asyncio.sleep(SSE_POLL_S)
                quiet += SSE_POLL_S
                if quiet >= SSE_PING_S:
                    quiet = 0.0
                    await resp.write(b": ping\n\n")
                job = await s.get(job["id"])
            await resp.write_eof()
        except ConnectionResetError:
            pass
        return resp

    def frame(typ, seq, data):
        return f"event: {typ}\nid: {seq}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()

    def coalesce(events):
        """Consecutive delta events become one (text joined, n summed, id of the last)."""
        out = []
        for seq, typ, data in events:
            if typ == "delta" and out and out[-1][1] == "delta":
                prev = out[-1]
                out[-1] = (seq, "delta", {"text": prev[2]["text"] + data["text"], "n": prev[2]["n"] + data["n"]})
            else:
                out.append((seq, typ, data))
        return out

    async def stream(request):
        s = svc(request)
        job = await s.get(request.match_info["id"])
        if job is None:
            return err("job not found", 404)
        hub = request.app[STREAM_HUB]
        jid = job["id"]
        try:
            last = max(0, int(request.headers.get("Last-Event-ID", "0")))
        except ValueError:
            last = 0
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})
        await resp.prepare(request)
        hub.subscribe(jid)
        try:
            pending = hub.events_since(jid, last) if last else None
            if pending is None:                                  # first connect, or Last-Event-ID is out of the buffer
                snap = hub.snapshot(jid)
                seq = snap["seq"]
                await resp.write(frame("snapshot", seq, snap))
            else:
                seq = last
            quiet, checked = 0.0, time.monotonic()
            while True:
                if time.monotonic() - checked >= STREAM_STATUS_S:
                    job, checked = await s.get(jid), time.monotonic()
                if job is None or job["status"] in TERMINAL:
                    evs = hub.events_since(jid, seq) or []
                    if evs:
                        await resp.write(b"".join(frame(t, q, d) for q, t, d in coalesce(evs)))
                    await resp.write(frame("end", hub.snapshot(jid)["seq"], {"status": job["status"] if job else "gone"}))
                    break
                if await hub.wait(jid, seq, STREAM_STATUS_S):
                    await asyncio.sleep(STREAM_COALESCE_S)
                    evs = hub.events_since(jid, seq)
                    if evs is None:
                        snap = hub.snapshot(jid)
                        seq = snap["seq"]
                        await resp.write(frame("snapshot", seq, snap))
                    elif evs:
                        seq = evs[-1][0]
                        await resp.write(b"".join(frame(t, q, d) for q, t, d in coalesce(evs)))
                    quiet = 0.0
                else:
                    quiet += STREAM_STATUS_S
                    if quiet >= SSE_PING_S:
                        quiet = 0.0
                        await resp.write(b": ping\n\n")
            await resp.write_eof()
        except (ConnectionResetError, ConnectionError):
            pass
        finally:
            hub.release(jid)
        return resp

    app.router.add_post("/api/jobs", create)
    app.router.add_get("/api/jobs", list_)
    app.router.add_get("/api/jobs/{id}", get_)
    app.router.add_post("/api/jobs/{id}/cancel", action("cancel"))
    app.router.add_post("/api/jobs/{id}/retry", action("retry"))
    app.router.add_get("/api/jobs/{id}/events", events)
    app.router.add_get("/api/jobs/{id}/stream", stream)
