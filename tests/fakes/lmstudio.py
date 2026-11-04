"""FakeLMStudio (a Transport that records calls and answers from recorded `lms` JSON) and FakeMemInfo.
HTTP mode (`await fake.start_http()` -> base url) also speaks POST /v1/chat/completions with scripted replies."""
import collections, copy, json, pathlib

from aiohttp import web

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


class FakeLMStudio:
    """Implements the llm.lmstudio_ctl.Transport protocol. `calls` = [("unload", id), ("load", key, id, ctx), ...].
    Loaded state follows load/unload. Pass loaded=[{"identifier":..., "modelKey":...}] to preload models."""

    def __init__(self, ls=None, loaded=None, reachable=True):
        self.ls = copy.deepcopy(ls if ls is not None else fixture("lms_ls.json"))
        template = fixture("lms_ps.json")[0]
        self._template = template
        self.loaded = [{**template, **m} for m in (loaded if loaded is not None else [])]
        self.up = reachable
        self.calls = []
        self.requests, self.replies, self.responder = [], collections.deque(), None     # HTTP mode
        self._runner, self.url = None, None

    def _need_up(self):
        if not self.up:
            from details.llm.lmstudio_ctl import LMStudioError
            raise LMStudioError("LM Studio is not reachable")

    async def reachable(self):
        return self.up

    async def list_downloaded(self):
        self._need_up()
        return copy.deepcopy(self.ls)

    async def list_loaded(self):
        self._need_up()
        return copy.deepcopy(self.loaded)

    async def load(self, model_key, identifier, context):
        self._need_up()
        self.calls.append(("load", model_key, identifier, context))
        self.loaded.append({**self._template, "modelKey": model_key, "identifier": identifier,
                            "contextLength": context})

    async def unload(self, identifier):
        self._need_up()
        self.calls.append(("unload", identifier))
        self.loaded = [m for m in self.loaded if m["identifier"] != identifier]

    # ---- HTTP mode (llm-outputs) ----------------------------------------------------------------------
    # script(*replies): each reply is consumed by one chat call. A reply is a str (the message content), a dict
    # (dumped to JSON as the content), or a dict with a "content" key plus optional "reasoning_content", "usage",
    # "finish_reason", or {"status": 500, "error": ...} for an HTTP error, or a callable(request_json) returning
    # any of those. When the script is empty `responder` answers; with neither, the call gets a 500.
    # `requests` holds every request body in order.
    def script(self, *replies):
        self.replies.extend(replies)
        return self

    async def start_http(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._chat)
        app.router.add_get("/v1/models", self._models)
        app.router.add_get("/api/v0/models", self._models)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    async def stop_http(self):
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    async def _models(self, request):
        return web.json_response({"data": []})

    async def _chat(self, request):
        body = await request.json()
        self.requests.append(body)
        reply = self.replies.popleft() if self.replies else self.responder
        if callable(reply):
            reply = reply(body)
        if reply is None:
            return web.json_response({"error": "no scripted reply"}, status=500)
        if not isinstance(reply, dict) or ("content" not in reply and "status" not in reply):
            reply = {"content": reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)}
        if "status" in reply:
            return web.json_response({"error": reply.get("error", "scripted error")}, status=reply["status"])
        text = reply["content"]
        prompt = sum(len(m.get("content", "")) for m in body.get("messages", []))
        message = {"role": "assistant", "content": text}
        if "reasoning_content" in reply:
            message["reasoning_content"] = reply["reasoning_content"]
        return web.json_response({
            "id": "chatcmpl-fake", "object": "chat.completion", "model": body.get("model"),
            "choices": [{"index": 0, "message": message, "finish_reason": reply.get("finish_reason", "stop")}],
            "usage": reply.get("usage", {"prompt_tokens": prompt // 4 + 1, "completion_tokens": len(text) // 4 + 1})})


class FakeMemInfo:
    def __init__(self, free_gb=64.0):
        self.free = free_gb

    def free_gb(self):
        return self.free
