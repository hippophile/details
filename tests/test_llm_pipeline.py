"""llm-outputs: validate / retry once / never repair, quote verification, map-reduce, cache, dedupe."""
import asyncio, json

import pytest

from details.llm import pipeline, schemas
from details.llm.client import LLMError
from llm_helpers import FULL, GOOD, SEGS, TEMPLATE, ScriptedChat, good


def gen(chat, mk_settings, kind="checklist", segments=SEGS, data_dir=None, context=100_000, **env):
    s = mk_settings(**env)

    async def go():
        return await pipeline.generate_kind(chat, settings=s, kind=kind, template=TEMPLATE, model="m", context=context,
                                            segments=segments, language="Greek", title="T",
                                            data_dir=data_dir)
    return asyncio.run(asyncio.wait_for(go(), 20))


def test_short_transcript_is_one_call_per_kind(mk_settings, tmp_path):
    for kind in schemas.KINDS:
        chat = ScriptedChat(good(kind))
        r = gen(chat, mk_settings, kind, data_dir=tmp_path)
        assert len(chat.calls) == 1 and r.mode == "single" and len(r.attempts) == 1
        assert r.content and r.attempts[0].errors is None and r.chunks == 1
        c = chat.calls[0]
        assert c["kind"] == kind and c["schema"] == schemas.model_schema(kind)
        assert c["max_tokens"] == mk_settings().llm.max_tokens(kind)
        text = c["messages"][0]["content"]
        assert "Greek" in text and "T" in text and SEGS[1] in text and "{{" not in text


def test_quotes_are_verified_and_counted(mk_settings, tmp_path):
    r = gen(ScriptedChat(good("checklist")), mk_settings, data_dir=tmp_path)
    assert [i["verified"] for i in r.content["items"]] == [True, True, True]
    assert (r.verified, r.unverified) == (3, 0)


def test_invented_quote_item_is_kept_unverified(mk_settings, tmp_path):
    c = good("checklist")
    c["items"][1]["quote"] = "Ο Νίκος θα απολύσει όλους"
    r = gen(ScriptedChat(c), mk_settings, data_dir=tmp_path)
    assert [i["verified"] for i in r.content["items"]] == [True, False, True]
    assert len(r.content["items"]) == 3 and (r.verified, r.unverified) == (2, 1)


def test_retry_once_with_the_validation_error_then_success(mk_settings, tmp_path):
    bad = good("checklist")
    bad["items"][0]["due"] = "next week"
    chat = ScriptedChat(bad, good("checklist"))
    r = gen(chat, mk_settings, data_dir=tmp_path)
    assert len(chat.calls) == 2 and len(r.attempts) == 2
    assert r.attempts[0].errors and r.attempts[1].errors is None and r.content
    retry_msgs = chat.calls[1]["messages"]
    assert retry_msgs[:1] == chat.calls[0]["messages"][:1] and len(retry_msgs) == 3
    assert "next week" in retry_msgs[-1]["content"] or "due" in retry_msgs[-1]["content"]
    assert retry_msgs[1]["role"] == "assistant"


def test_two_failures_means_no_content_and_raw_text_kept_never_repaired(mk_settings, tmp_path):
    chat = ScriptedChat("{not json", '{"schema_version":1,"title":"x"}')
    r = gen(chat, mk_settings, data_dir=tmp_path)
    assert len(chat.calls) == 2 and r.content is None
    assert [a.valid for a in r.attempts] == [False, False]
    assert r.attempts[-1].text == '{"schema_version":1,"title":"x"}'     # exactly what the model said


def test_llm_error_propagates(mk_settings, tmp_path):
    with pytest.raises(LLMError):
        gen(ScriptedChat(LLMError("LM Studio not reachable at http://x")), mk_settings, data_dir=tmp_path)


# ---- map-reduce ------------------------------------------------------------------------------------------

LONG = [f"Segment {i} θα κάνουμε deploy στις {i} Νοεμβρίου και ελέγχουμε." for i in range(6)]
ENV = dict(DETAILS_LLM_CHARS_PER_TOKEN="1", DETAILS_LLM_SINGLE_PASS_MAX_TOKENS="100", DETAILS_LLM_CHUNK_TOKENS="130",
           DETAILS_LLM_CHUNK_OVERLAP_TOKENS="0")


def item(i, text=None):
    return {"text": text or f"Deploy {i}", "owner": None, "due": None, "done": False,
            "quote": f"θα κάνουμε deploy στις {i} Νοεμβρίου"}


def chunk_reply(call):
    seg_ids = sorted({int(w) for w in __import__("re").findall(r"Segment (\d+)", call["messages"][0]["content"])})
    return {"schema_version": 1, "title": "T", "items": [item(i) for i in seg_ids]}


def test_long_transcript_makes_one_map_call_per_chunk_on_whole_segments(mk_settings, tmp_path):
    chat = ScriptedChat(default=chunk_reply)
    r = gen(chat, mk_settings, segments=LONG, data_dir=tmp_path, **ENV)
    assert r.mode == "map-reduce" and r.chunks == 3 and len(chat.calls) == 3
    sent = [call["messages"][0]["content"] for call in chat.calls]
    for s in LONG:                                  # every segment arrives whole in some chunk
        assert any(s in m for m in sent)
    assert [i["text"] for i in r.content["items"]] == [f"Deploy {i}" for i in range(6)]
    assert r.verified == 6 and r.unverified == 0    # verified against the full transcript


def test_duplicate_items_across_chunks_appear_once(mk_settings, tmp_path):
    def same(call):
        return {"schema_version": 1, "title": "T", "items": [item(1, "Deploy στις 1 Νοεμβρίου"),
                                                            item(2, "DEPLOY ΣΤΙΣ 1 Νοεμβρίου")]}
    r = gen(ScriptedChat(default=same), mk_settings, segments=LONG, data_dir=tmp_path, **ENV)
    assert len(r.content["items"]) == 1


def test_resume_uses_the_cache_only_the_missing_chunk_is_sent(mk_settings, tmp_path):
    first = ScriptedChat(chunk_reply, chunk_reply, LLMError("LM Studio died"))
    with pytest.raises(LLMError):
        gen(first, mk_settings, segments=LONG, data_dir=tmp_path, **ENV)
    assert len(first.calls) == 3
    second = ScriptedChat(default=chunk_reply)
    r = gen(second, mk_settings, segments=LONG, data_dir=tmp_path, **ENV)
    assert len(second.calls) == 1 and r.cache_hits == 2
    assert "Segment 5" in second.calls[0]["messages"][0]["content"]
    assert [i["text"] for i in r.content["items"]] == [f"Deploy {i}" for i in range(6)]


def test_cache_is_keyed_by_model(mk_settings, tmp_path):
    gen(ScriptedChat(default=chunk_reply), mk_settings, segments=LONG, data_dir=tmp_path, **ENV)
    s = mk_settings(**ENV)

    async def other_model():
        return await pipeline.generate_kind(ScriptedChat(default=chunk_reply), settings=s, kind="checklist",
                                            template=TEMPLATE, model="other", context=100_000, segments=LONG,
                                            language="Greek", title="T", data_dir=tmp_path)
    assert asyncio.run(other_model()).cache_hits == 0


def test_debrief_summary_comes_from_one_reduce_call_and_lists_are_merged(mk_settings, tmp_path):
    def reply(call):
        if call["kind"] == "reduce":
            return {"text": "Τελική σύνοψη"}
        c = good("debrief", summary="μερική")
        return c
    chat = ScriptedChat(default=reply)
    r = gen(chat, mk_settings, kind="debrief", segments=LONG, data_dir=tmp_path, **ENV)
    assert [c["kind"] for c in chat.calls] == ["debrief"] * 3 + ["reduce"]
    assert r.content["summary"] == "Τελική σύνοψη"
    assert len(r.content["decisions"]) == 1                      # identical across chunks -> once
    assert "μερική" in chat.calls[-1]["messages"][0]["content"]


def test_tags_are_ranked_by_frequency_and_capped_at_eight(mk_settings, tmp_path):
    names = [f"tag{i}" for i in range(10)]
    seq = [{"schema_version": 1, "tags": [{"name": n, "quote": "deploy"} for n in names[:6]]},
           {"schema_version": 1, "tags": [{"name": n, "quote": "deploy"} for n in ["tag9", "tag8", "tag7", "tag6", "tag3"]]},
           {"schema_version": 1, "tags": [{"name": "tag3", "quote": "deploy"}, {"name": "tag9", "quote": "deploy"}]}]
    chat = ScriptedChat(*seq)
    r = gen(chat, mk_settings, kind="tags", segments=LONG, data_dir=tmp_path, **ENV)
    got = [t["name"] for t in r.content["tags"]]
    assert len(got) == 8 and got[:2] == ["tag3", "tag9"]
    assert len(chat.calls) == 3                                   # no reduce call for tags


def test_chunk_that_fails_twice_fails_the_kind_without_content(mk_settings, tmp_path):
    chat = ScriptedChat(chunk_reply, "garbage", "garbage")
    r = gen(chat, mk_settings, segments=LONG, data_dir=tmp_path, **ENV)
    assert r.content is None and r.attempts[-1].errors and r.attempts[-1].text == "garbage"


def test_model_context_caps_the_single_pass_size(mk_settings, tmp_path):
    chat = ScriptedChat(default=chunk_reply)
    text_len = sum(len(s) for s in LONG)
    env = dict(DETAILS_LLM_CHARS_PER_TOKEN="1", DETAILS_LLM_SINGLE_PASS_MAX_TOKENS="20000",
               DETAILS_LLM_CHUNK_TOKENS="8000", DETAILS_LLM_CHUNK_OVERLAP_TOKENS="0")
    ctx = pipeline.PROMPT_OVERHEAD_TOKENS + mk_settings().llm.max_tokens("checklist") + text_len - 50
    r = gen(chat, mk_settings, segments=LONG, data_dir=tmp_path, context=ctx, **env)
    assert r.mode == "map-reduce" and r.chunks >= 2
