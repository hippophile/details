"""llm-outputs: LM Studio structured-output client against FakeLMStudio in HTTP mode."""
import asyncio, json

import pytest

from details.llm import client, schemas
from fakes.lmstudio import FakeLMStudio
from llm_helpers import good


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


def call(fake, mk_settings, kind="tags", **env):
    async def go():
        url = fake.url or await fake.start_http()
        s = mk_settings(DETAILS_LMSTUDIO_URL=url, **env)
        try:
            return await client.chat(s, model="details-gemma-cheap", kind=kind, schema=schemas.model_schema(kind),
                                     messages=[{"role": "user", "content": "hi"}], max_tokens=321)
        finally:
            await fake.stop_http()
    return run(go())


def test_request_shape_and_url_from_settings(mk_settings):
    fake = FakeLMStudio().script(good("tags"))
    out = call(fake, mk_settings)
    req = fake.requests[0]
    assert req["response_format"]["type"] == "json_schema"
    assert req["response_format"]["json_schema"]["name"] == "tags"
    assert req["response_format"]["json_schema"]["strict"] is True
    assert req["response_format"]["json_schema"]["schema"] == schemas.model_schema("tags")
    assert req["reasoning_effort"] == "none" and req["temperature"] == 0.1 and req["max_tokens"] == 321
    assert req["model"] == "details-gemma-cheap" and req["messages"] == [{"role": "user", "content": "hi"}]
    assert json.loads(out["text"]) == good("tags")
    assert out["tokens_in"] > 0 and out["tokens_out"] > 0 and out["finish_reason"] == "stop"
    assert out["wall_ms"] >= 0 and out["raw"]["choices"]


def test_reasoning_content_is_ignored(mk_settings):
    fake = FakeLMStudio().script({"content": json.dumps(good("tags")), "reasoning_content": "let me think... {not json"})
    out = call(fake, mk_settings)
    assert json.loads(out["text"]) == good("tags")


def test_unreachable_url_is_named_in_the_error(mk_settings):
    s = mk_settings(DETAILS_LMSTUDIO_URL="http://127.0.0.1:9")
    with pytest.raises(client.LLMError, match=r"http://127\.0\.0\.1:9"):
        run(client.chat(s, model="m", kind="tags", schema=schemas.model_schema("tags"),
                        messages=[{"role": "user", "content": "x"}], max_tokens=10))


def test_http_error_is_reported_with_status(mk_settings):
    fake = FakeLMStudio().script({"status": 500, "error": "model crashed"})
    with pytest.raises(client.LLMError, match=r"500.*model crashed"):
        call(fake, mk_settings)
