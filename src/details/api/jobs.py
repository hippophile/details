"""Jobs API: POST/GET /api/jobs, GET /api/jobs/{id}, POST cancel|retry, SSE /api/jobs/{id}/events."""
import asyncio, json

from aiohttp import web

from ..services.jobs import TERMINAL, JobError, JobService, NotFound, WrongState

JOBS = web.AppKey("jobs", JobService)
SSE_POLL_S = 0.25
SSE_PING_S = 15


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

    def state(job, seq):
        return {"seq": seq, "job": job["id"], "status": job["status"], "step": job["step"],
                "pct": job["step_progress"], "msg": job["message"]}

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
                cur = state(job, 0)
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

    app.router.add_post("/api/jobs", create)
    app.router.add_get("/api/jobs", list_)
    app.router.add_get("/api/jobs/{id}", get_)
    app.router.add_post("/api/jobs/{id}/cancel", action("cancel"))
    app.router.add_post("/api/jobs/{id}/retry", action("retry"))
    app.router.add_get("/api/jobs/{id}/events", events)
