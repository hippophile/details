"""WebSocket /api/record/ws and GET /api/scripts."""
import asyncio, json, logging, uuid

from aiohttp import WSMsgType, web

from ..services import recordings, scripts

log = logging.getLogger("details.record")
SESSIONS = web.AppKey("record_sessions", dict)


def add_record_routes(app, settings, pool_key):
    app[SESSIONS] = {}

    async def list_scripts(request):
        rows = await scripts.list_scripts(request.app[pool_key])
        return web.json_response(rows)

    async def grace_expiry(sess):
        try:
            await asyncio.sleep(settings.record.grace_s)
            await recordings.finalize(app[pool_key], settings, sess, interrupted=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("auto-finalize failed for %s (recovery will retry at startup)", sess.id)

    async def ws_handler(request):
        ws = web.WebSocketResponse(max_msg_size=settings.record.max_msg_mb * 1024 * 1024)
        await ws.prepare(request)
        pool = app[pool_key]
        sess = None

        async def err(msg):
            await ws.send_json({"type": "error", "message": msg})

        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    if sess and sess.state == "recording":
                        await asyncio.to_thread(sess.writer.write, msg.data) if len(msg.data) > 65536 \
                            else sess.writer.write(msg.data)
                    continue
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                    kind = m["type"]
                except (ValueError, KeyError, TypeError):
                    await err("bad message")
                    continue
                if kind == "start" and sess is None:
                    try:
                        sess = recordings.create_session(settings, (m.get("title") or "").strip(),
                                                         m.get("script_id"), m.get("sample_rate"))
                    except recordings.PreflightError as e:
                        await err(str(e))
                        continue
                    sess.attached = True
                    app[SESSIONS][sess.id] = sess
                    await ws.send_json({"type": "ready", "recording_id": sess.id})
                elif kind == "resume" and sess is None:
                    try:
                        found = app[SESSIONS].get(str(uuid.UUID(str(m.get("recording_id")))))
                    except ValueError:
                        found = None
                    if found is None:
                        await err("unknown recording")
                    elif found.state in ("finalizing", "finalized"):
                        await err("already finalized")
                    elif found.attached:
                        await err("recording already has an open connection")
                    else:
                        if found.grace_task:
                            found.grace_task.cancel()
                            found.grace_task = None
                        found.state, found.attached, sess = "recording", True, found
                        await ws.send_json({"type": "ready", "recording_id": found.id})
                elif kind == "gap" and sess:
                    sess.gaps.append({"ms": m.get("ms"), "at_frames": sess.writer.frames})
                elif kind == "stop" and sess:
                    res = await recordings.finalize(pool, settings, sess)
                    sess.attached = False
                    if res is None:
                        await ws.send_json({"type": "empty"})
                    else:
                        await ws.send_json({"type": "saved", "recording": res})
                    sess = None
                    await ws.close()
                else:
                    await err("unexpected message")
        finally:
            if sess is not None and sess.state == "recording":
                sess.state, sess.attached = "interrupted", False
                sess.writer.sync()
                sess.grace_task = asyncio.create_task(grace_expiry(sess))
        return ws

    async def cleanup(app):
        for s in app[SESSIONS].values():
            if s.grace_task:
                s.grace_task.cancel()
            if s.writer and s.state in ("recording", "interrupted"):
                s.writer.close()

    app.router.add_get("/api/scripts", list_scripts)
    app.router.add_get("/api/record/ws", ws_handler)
    app.on_cleanup.append(cleanup)
