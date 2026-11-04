"""llm-streaming: chat_stream against FakeLMStudio (scripted SSE) and the SSE fixtures recorded from LM Studio."""
import asyncio, json, pathlib, time

import pytest

from details.llm import client, schemas
from details.llm.client import LLMError, StreamCancelled, StreamUnsupported
from fakes.lmstudio import FakeLMStudio
from llm_helpers import good

FIX = pathlib.Path(__file__).parent / "fixtures"
SECRET = "password=SeCr3tPw"


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


def stream(fake, mk_settings, kind="tags", deltas=None, cancelled=None, after=None, **env):
    """-> result of chat_stream; `deltas` collects the on_delta pieces."""
    async def go():
        url = fake.url or await fake.start_http()
        s = mk_settings(DETAILS_LMSTUDIO_URL=url, **env)
        try:
            return await client.chat_stream(s, model="details-gemma-cheap", kind=kind,
                                            schema=schemas.model_schema(kind),
                                            messages=[{"role": "user", "content": "hi"}], max_tokens=321,
                                            on_delta=(deltas.append if deltas is not None else None),
                                            cancelled=cancelled)
        finally:
            if after:
                await after(fake)
            await fake.stop_http()
    return run(go())


def reply(kind="tags", **opts):
    text = json.dumps(good(kind), ensure_ascii=False)
    return {"content": text, "stream": opts}


def test_request_shape(mk_settings):
    fake = FakeLMStudio().script(reply())
    stream(fake, mk_settings)
    req = fake.requests[0]
    assert req["stream"] is True and req["stream_options"] == {"include_usage": True}
    assert req["response_format"]["json_schema"]["strict"] is True and req["response_format"]["json_schema"]["name"] == "tags"
    assert req["reasoning_effort"] == "none" and req["temperature"] == 0.1 and req["max_tokens"] == 321


def test_same_result_as_a_plain_call(mk_settings):
    text = json.dumps(good("tags"), ensure_ascii=False)
    pieces = [text[i:i + 8] for i in range(0, len(text), 8)][:11]
    pieces.append(text[88:])                             # 12 pieces, in order
    assert "".join(pieces) == text and len(pieces) == 12
    fake = FakeLMStudio().script({"content": text, "usage": {"prompt_tokens": 40, "completion_tokens": 77},
                                  "stream": {"pieces": pieces}})
    got = []
    out = stream(fake, mk_settings, deltas=got)
    assert out["text"] == text and out["tokens_in"] == 40 and out["tokens_out"] == 77
    assert out["finish_reason"] == "stop" and out["wall_ms"] >= 0
    assert got == pieces
    assert out["raw"]["stream"] is True and out["raw"]["usage"]["completion_tokens"] == 77
    assert out["raw"]["finish_reason"] == "stop" and out["raw"]["chunks"] >= 12
    assert set(out) == {"text", "tokens_in", "tokens_out", "finish_reason", "wall_ms", "raw"}


def test_reasoning_deltas_are_ignored(mk_settings):
    fake = FakeLMStudio().script(reply(reasoning=["thinking about", " the answer"]))
    got = []
    out = stream(fake, mk_settings, deltas=got)
    assert "thinking" not in out["text"] and "thinking" not in "".join(got)
    assert json.loads(out["text"]) == good("tags")


def test_no_usage_chunk_estimates_tokens(mk_settings):
    fake = FakeLMStudio().script(reply(no_usage=True))
    got = []
    out = stream(fake, mk_settings, deltas=got)
    assert out["tokens_out"] == len(got) > 0 and out["tokens_in"] == 0
    assert out["raw"]["usage_estimated"] is True


def test_recorded_lm_studio_fixture_ok(mk_settings):
    raw = (FIX / "lmstudio_stream_ok.sse").read_bytes()
    fake = FakeLMStudio().script({"content": "x", "stream": {"raw": raw}})
    got = []
    out = stream(fake, mk_settings, kind="checklist", deltas=got)
    data = json.loads(out["text"])
    assert "title" in data and isinstance(data["items"], list)
    assert "".join(got) == out["text"] and out["finish_reason"] == "stop"
    assert out["tokens_out"] > 0 and out["tokens_in"] > 0 and "usage_estimated" not in out["raw"]


def test_recorded_lm_studio_fixture_length(mk_settings):
    raw = (FIX / "lmstudio_stream_length.sse").read_bytes()
    fake = FakeLMStudio().script({"content": "x", "stream": {"raw": raw}})
    out = stream(fake, mk_settings, kind="checklist")
    assert out["finish_reason"] == "length" and out["text"]


def test_parser_tolerates_comments_crlf_and_split_lines(mk_settings):
    c = lambda t: json.dumps({"choices": [{"delta": {"content": t}, "finish_reason": None}]})
    done = json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    line = f"data: {c('{\"a\"')}\r\n\r\n".encode()
    parts = [b": keepalive\r\n\r\n", line[:10], line[10:25], line[25:], f"data: {c(':1}')}\n\n".encode(),
             f"data: {done}\n\n".encode(), b"data: [DONE]\n\n"]
    fake = FakeLMStudio().script({"content": "x", "stream": {"raw": parts}})
    got = []
    out = stream(fake, mk_settings, deltas=got)
    assert out["text"] == '{"a":1}' and got == ['{"a"', ":1}"]


def test_idle_timeout_closes_the_connection(mk_settings):
    fake = FakeLMStudio().script(reply(stall_after=3, delay=0.01))

    async def check(f):
        await asyncio.wait_for(f.disconnected.wait(), 3)
    with pytest.raises(LLMError) as ei:
        stream(fake, mk_settings, DETAILS_LLM_STREAM_IDLE_TIMEOUT_S="0.4", after=check)
    assert ei.value.status == "timeout" and "LM Studio" in str(ei.value) and not isinstance(ei.value, StreamUnsupported)


def test_drop_after_first_delta_is_an_error_without_second_request(mk_settings):
    fake = FakeLMStudio().script(reply(drop_after=3), reply())
    with pytest.raises(LLMError) as ei:
        stream(fake, mk_settings)
    assert "stream ended early" in str(ei.value) and "http://127.0.0.1" in str(ei.value)
    assert not isinstance(ei.value, StreamUnsupported) and len(fake.requests) == 1


def test_error_chunk_after_deltas_is_redacted_and_not_a_fallback(mk_settings):
    fake = FakeLMStudio().script(reply(error=f"model crashed {SECRET}"))
    with pytest.raises(LLMError) as ei:
        stream(fake, mk_settings)
    assert "model crashed" in str(ei.value) and "SeCr3tPw" not in str(ei.value)
    assert not isinstance(ei.value, StreamUnsupported) and len(fake.requests) == 1


@pytest.mark.parametrize("script,why", [
    ({"status": 400, "error": "stream is not supported"}, "400"),
    ({"status": 422, "error": "unknown field stream_options"}, "422"),
    ({"content": "{}", "stream": {"plain": True}}, "sse"),
    ({"content": "{}", "stream": {"plain": True, "content_type": "text/html"}}, "sse"),
    ({"content": "", "stream": {"pieces": []}}, "content"),
])
def test_failures_before_the_first_delta_ask_for_the_plain_fallback(mk_settings, script, why):
    fake = FakeLMStudio().script(script)
    with pytest.raises(StreamUnsupported) as ei:
        stream(fake, mk_settings)
    assert why in ei.value.reason.lower() and isinstance(ei.value, LLMError) and len(fake.requests) == 1


def test_other_http_errors_do_not_ask_for_fallback(mk_settings):
    fake = FakeLMStudio().script({"status": 500, "error": "model crashed"})
    with pytest.raises(LLMError) as ei:
        stream(fake, mk_settings)
    assert not isinstance(ei.value, StreamUnsupported) and ei.value.status == "http 500"


def test_unreachable(mk_settings):
    fake = FakeLMStudio()
    fake.url = "http://127.0.0.1:9"
    with pytest.raises(LLMError) as ei:
        stream(fake, mk_settings)
    assert ei.value.status == "unreachable"


def test_cancel_closes_the_connection_within_a_second(mk_settings):
    fake = FakeLMStudio().script(reply(delay=0.2))
    cancelled, t = asyncio.Event(), {}
    got = []

    def on_delta(piece):
        got.append(piece)
        if len(got) == 3:
            t["cancel"] = time.monotonic()
            cancelled.set()

    async def go():
        url = await fake.start_http()
        s = mk_settings(DETAILS_LMSTUDIO_URL=url)
        try:
            with pytest.raises(StreamCancelled) as ei:
                await client.chat_stream(s, model="m", kind="tags", schema=schemas.model_schema("tags"),
                                         messages=[{"role": "user", "content": "hi"}], max_tokens=50,
                                         on_delta=on_delta, cancelled=cancelled)
            assert not isinstance(ei.value, LLMError)
            await asyncio.wait_for(fake.disconnected.wait(), 2)
        finally:
            await fake.stop_http()
    run(go())
    assert fake.disconnect_at - t["cancel"] < 1.0 and len(got) == 3


# ---- real LM Studio (gpu marker, run by hand): the already-loaded gemma-4-e4b, no load/unload ------------------

@pytest.mark.gpu
def test_real_e4b_streams_a_checklist_that_matches_the_plain_result_structurally(tmp_path):
    import urllib.request
    from conftest import make_settings
    from details.llm import pipeline
    from details.llm.prompts import parse_template
    from details.settings import REPO_ROOT
    from llm_helpers import SEGS
    model = "google/gemma-4-e4b"
    s = make_settings(tmp_path / "data")
    try:
        with urllib.request.urlopen(s.lmstudio.url + "/api/v0/models", timeout=3) as r:
            ok = any(m["id"] == model and m.get("state") == "loaded" for m in json.load(r)["data"])
    except Exception:
        ok = False
    if not ok:
        pytest.skip(f"{model} is not loaded in LM Studio at the configured URL")
    t = parse_template(REPO_ROOT / "prompts" / "checklist" / "v1.md")
    deltas = []

    def gen(chat_fn):
        return asyncio.run(asyncio.wait_for(pipeline.generate_kind(
            chat_fn, settings=s, kind="checklist", template=t, model=model, context=8192, segments=SEGS,
            language="Greek", title="Test", data_dir=tmp_path), 300))

    async def streamed(messages, *, kind, schema, max_tokens):
        return await client.chat_stream(s, model=model, kind=kind, schema=schema, messages=messages,
                                        max_tokens=max_tokens, on_delta=deltas.append)

    async def plain(messages, *, kind, schema, max_tokens):
        return await client.chat(s, model=model, kind=kind, schema=schema, messages=messages, max_tokens=max_tokens)
    a, b = gen(streamed), gen(plain)
    assert a.attempts[-1].valid and b.attempts[-1].valid, (a.attempts[-1].errors, b.attempts[-1].errors)
    assert a.attempts[-1].raw["stream"] is True and len(deltas) > 5
    assert "".join(deltas) == a.attempts[0].text or len(a.attempts) > 1
    assert set(a.content) == set(b.content) and a.content["schema_version"] == b.content["schema_version"]
    assert all(set(i) == set(b.content["items"][0]) for i in a.content["items"]) and a.verified >= 1
