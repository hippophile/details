"""WebSocket /api/record/ws and GET /api/scripts."""
import asyncio, json, logging, uuid

from aiohttp import WSMsgType, web

from ..live.session import LIVE
from ..services import recordings, scripts

log = logging.getLogger("details.record")
SESSIONS = web.AppKey("record_sessions", dict)


def add_record_routes(app, settings, pool_key):
    app[SESSIONS] = {}

    async def list_scripts(request):
        rows = await scripts.list_scripts(request.app[pool_key])
        return web.json_response(rows)

    async def close_live(sess):
        """End live assist (kill the child, release the GPU lease) within its own short deadline. Never raises."""
        if sess.live is not None:
            try:
                await sess.live.close()
            except Exception:
                log.exception("closing live assist failed for %s", sess.id)

    async def grace_expiry(sess):
        try:
            await asyncio.sleep(settings.record.grace_s)
            await close_live(sess)
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
                        live = sess.live
                        before = sess.writer.frames if live is not None else 0
                        await asyncio.to_thread(sess.writer.write, msg.data) if len(msg.data) > 65536 \
                            else sess.writer.write(msg.data)
                        if live is not None and not live.failed:      # after the write; a bug here only ends live
                            try:
                                live.offer(msg.data, before)
                            except Exception as e:
                                log.exception("live offer raised for %s; live is off", sess.id)
                                live.mark_failed(e)
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
                    if m.get("live") is True and LIVE in app:
                        try:
                            sess.live = app[LIVE].begin(sess, m.get("silence_db"))
                            sess.live.attach(ws.send_json)
                        except Exception:
                            log.exception("live assist could not start for %s", sess.id)
                            await ws.send_json({"type": "live_state", "state": "failed",
                                                "message": "Live assist could not start. Recording continues"})
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
                        if found.live is not None:
                            found.live.attach(ws.send_json, resume=True)
                elif kind == "gap" and sess:
                    sess.gaps.append({"ms": m.get("ms"), "at_frames": sess.writer.frames})
                elif kind == "stop" and sess:
                    await close_live(sess)
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
            if sess is not None and sess.live is not None:
                sess.live.detach()
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
