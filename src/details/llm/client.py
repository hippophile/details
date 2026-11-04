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
