"""LM Studio structured-output client: POST {settings.lmstudio.url}/v1/chat/completions with response_format
json_schema, reasoning_effort "none". Reads message.content only (never reasoning_content)."""
import asyncio, json, time

import aiohttp

from ..runner.joblog import redact, secrets_from


class LLMError(RuntimeError):
    """`status` is the short outcome logged per call: http <code> | timeout | unreachable | invalid-json."""

    def __init__(self, message, status="error"):
        super().__init__(message)
        self.status = status


async def chat(settings, *, model, kind, schema, messages, max_tokens):
    """-> {text, tokens_in, tokens_out, finish_reason, wall_ms, raw}. Raises LLMError naming the URL on failure."""
    secrets = secrets_from(settings)
    base = settings.lmstudio.url.rstrip("/")
    shown = redact(base, secrets)                                    # the URL may carry credentials
    body = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": max_tokens,
            "reasoning_effort": "none", "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {"name": kind, "strict": True, "schema": schema}}}
    t0 = time.monotonic()
    try:
        timeout = aiohttp.ClientTimeout(total=settings.llm.timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(base + "/v1/chat/completions", json=body) as r:
                text = await r.text()
                if r.status != 200:
                    raise LLMError(f"LM Studio at {shown} answered {r.status}: {redact(text[:300], secrets)}",
                                   f"http {r.status}")
                try:
                    raw = json.loads(text)
                except ValueError:
                    raise LLMError(f"LM Studio at {shown} returned a non-JSON body", "invalid-json") from None
    except LLMError:
        raise
    except asyncio.TimeoutError:
        raise LLMError(f"LM Studio at {shown} did not answer within {settings.llm.timeout_s:g} s", "timeout") from None
    except aiohttp.ClientError as e:
        raise LLMError(f"cannot reach LM Studio at {shown}: {type(e).__name__}", "unreachable") from None
    wall_ms = round((time.monotonic() - t0) * 1000)
    try:
        choice = raw["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"LM Studio at {shown} returned an unexpected reply shape", "invalid-json") from None
    usage = raw.get("usage") or {}
    return {"text": content or "", "tokens_in": usage.get("prompt_tokens") or 0,
            "tokens_out": usage.get("completion_tokens") or 0, "finish_reason": choice.get("finish_reason"),
            "wall_ms": wall_ms, "raw": raw}



class StreamCancelled(Exception):
    """The job was cancelled while a reply was streaming. NOT an LLMError: the step must not record a failure."""


class StreamUnsupported(LLMError):
    """The streaming request failed before the first content delta in a way a plain call may avoid. The caller
    falls back to `chat` once; `reason` is what the call log shows."""

    def __init__(self, message, reason):
        super().__init__(message, "stream-unsupported")
        self.reason = reason


async def _read(task_factory, cancel_wait, timeout):
    """Await one read, racing the cancel event and the idle timeout. -> bytes. Raises StreamCancelled/TimeoutError."""
    task = asyncio.ensure_future(task_factory())
    try:
        done, _ = await asyncio.wait({task, cancel_wait}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        if cancel_wait in done:
            raise StreamCancelled()
        raise asyncio.TimeoutError()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def chat_stream(settings, *, model, kind, schema, messages, max_tokens, on_delta=None, cancelled=None):
    """Same contract as `chat` ({text, tokens_in, tokens_out, finish_reason, wall_ms, raw}) but the reply is read as
    server-sent events. `on_delta(piece)` gets every non-empty `delta.content` (never `reasoning_content`).
    Raises StreamUnsupported (before the first delta only), StreamCancelled (`cancelled` event set; the connection
    is closed, so LM Studio stops generating) or LLMError. The first byte may take long (prompt processing), so it
    gets settings.llm.timeout_s; after that a silent socket fails after settings.llm.stream_idle_timeout_s."""
    secrets = secrets_from(settings)
    base = settings.lmstudio.url.rstrip("/")
    shown = redact(base, secrets)
    body = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": max_tokens,
            "reasoning_effort": "none", "stream": True, "stream_options": {"include_usage": True},
            "response_format": {"type": "json_schema", "json_schema": {"name": kind, "strict": True, "schema": schema}}}
    idle = settings.llm.stream_idle_timeout_s
    t0 = time.monotonic()
    parts, pieces, chunks, usage, finish, ttft_ms = [], 0, 0, None, None, None
    cancel_wait = asyncio.ensure_future(cancelled.wait()) if cancelled is not None else asyncio.ensure_future(asyncio.Event().wait())
    done_marker = False
    try:
        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=min(30, settings.llm.timeout_s))
            async with aiohttp.ClientSession(timeout=timeout) as s:
                connect = asyncio.ensure_future(s.post(base + "/v1/chat/completions", json=body))
                try:
                    d, _ = await asyncio.wait({connect, cancel_wait}, timeout=settings.llm.timeout_s,
                                              return_when=asyncio.FIRST_COMPLETED)
                    if connect not in d:
                        connect.cancel()
                        await asyncio.gather(connect, return_exceptions=True)
                        if cancel_wait in d:
                            raise StreamCancelled()
                        raise asyncio.TimeoutError()
                    r = connect.result()
                finally:
                    if not connect.done():
                        connect.cancel()
                async with r:
                    if r.status != 200:
                        text = (await r.text())[:300]
                        msg = f"LM Studio at {shown} answered {r.status}: {redact(text, secrets)}"
                        if r.status in (400, 422) and "stream" in text.lower():
                            raise StreamUnsupported(msg, f"http {r.status}")
                        raise LLMError(msg, f"http {r.status}")
                    if "text/event-stream" not in r.headers.get("Content-Type", "").lower():
                        raise StreamUnsupported(f"LM Studio at {shown} did not answer with an event stream",
                                                "non-sse response")
                    wait = settings.llm.timeout_s              # until the first byte of the reply
                    while True:
                        try:
                            raw_line = await _read(r.content.readline, cancel_wait, wait)
                        except asyncio.TimeoutError:
                            raise LLMError(f"LM Studio at {shown} sent nothing for {wait:g} s", "timeout") from None
                        if not raw_line:
                            break                                # EOF
                        wait = idle
                        line = raw_line.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue                             # blank separator, ":" comment, event:/id: fields
                        data = line[5:].strip()
                        if data == "[DONE]":
                            done_marker = True
                            break
                        try:
                            ev = json.loads(data)
                        except ValueError:
                            continue
                        if not isinstance(ev, dict):
                            continue
                        chunks += 1
                        if ev.get("error"):
                            err = ev["error"]
                            msg = err.get("message") if isinstance(err, dict) else err
                            raise LLMError(f"LM Studio at {shown} reported an error: "
                                           f"{redact(str(msg)[:300], secrets)}", "error")
                        if ev.get("usage"):
                            usage = ev["usage"]
                        ch = (ev.get("choices") or [{}])[0] if isinstance(ev.get("choices"), list) else {}
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]
                        piece = (ch.get("delta") or {}).get("content")
                        if piece:
                            if ttft_ms is None:
                                ttft_ms = round((time.monotonic() - t0) * 1000)
                            parts.append(piece)
                            pieces += 1
                            if on_delta is not None:
                                on_delta(piece)
        finally:
            cancel_wait.cancel()
    except (LLMError, StreamCancelled):
        raise
    except asyncio.TimeoutError:
        raise LLMError(f"LM Studio at {shown} did not answer within {settings.llm.timeout_s:g} s", "timeout") from None
    except aiohttp.ClientError as e:
        if pieces:
            raise LLMError(f"LM Studio at {shown} closed the stream early: {type(e).__name__}", "stream-ended-early") from None
        raise LLMError(f"cannot reach LM Studio at {shown}: {type(e).__name__}", "unreachable") from None
    if not pieces:
        raise StreamUnsupported(f"LM Studio at {shown} streamed no content", "no content" if chunks else "no parsable chunk")
    if not done_marker and not finish:
        raise LLMError(f"LM Studio at {shown}: stream ended early", "stream-ended-early")
    wall_ms = round((time.monotonic() - t0) * 1000)
    raw = {"stream": True, "chunks": chunks, "usage": usage, "finish_reason": finish, "ttft_ms": ttft_ms}
    if usage and usage.get("completion_tokens") is not None:
        tin, tout = usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0
    else:
        tin, tout = 0, pieces
        raw["usage_estimated"] = True            # estimate, not measured: one token per streamed piece
    return {"text": "".join(parts), "tokens_in": tin, "tokens_out": tout, "finish_reason": finish,
            "wall_ms": wall_ms, "raw": raw}
