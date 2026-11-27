"""llm-outputs: validate / retry once / never repair, quote verification, map-reduce, cache, dedupe."""
import asyncio, json, re, types

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


# ---- brief (monday-brief): ids and order in code, the link call, merge across chunks ---------------------------

from details.llm.client import StreamCancelled
from llm_helpers import LINK_NONE

TIMED = [(i * 1000, t) for i, t in enumerate(SEGS)]
BRIEF_TEMPLATE = types.SimpleNamespace(id=None, name="brief", version=1, body="{{transcript}}", sha256="c" * 64, checks=())


def genb(chat, mk_settings, segments=SEGS, timed="default", data_dir=None, template=BRIEF_TEMPLATE, **env):
    s = mk_settings(**env)
    timed = [(i * 1000, t) for i, t in enumerate(segments)] if timed == "default" else timed

    async def go():
        return await pipeline.generate_kind(chat, settings=s, kind="brief", template=template, model="m",
                                            context=100_000, segments=segments, language="Greek", title="T",
                                            data_dir=data_dir, timed_segments=timed)
    return asyncio.run(asyncio.wait_for(go(), 20))


def brief3():
    """Model order is NOT transcript order; the last decision's quote is not in the transcript."""
    c = good("brief")
    c["decisions"] = [{"text": "Documentation", "quote": "ετοιμάσει το documentation"},               # 2 s
                      {"text": "Deploy 12/11", "quote": "Θα κάνουμε deploy στις 12 Νοεμβρίου"},      # 1 s
                      {"text": "Μοντέλο", "quote": "ποιο μοντέλο"},                                   # 4 s
                      {"text": "Δεν ειπώθηκε", "quote": "κάτι που δεν υπάρχει"}]                     # unlocated
    p = lambda who, st, q: {"who": who, "stance": st, "quote": q}
    c["disagreements"] = [{"topic": "Late", "positions": [p("A", "a", "ρίσκο με τα δεδομένα"), p("B", "b", "ποιο μοντέλο")]},
                          {"topic": "Early", "positions": [p("A", "a", "Καλημέρα σε όλους"), p("B", "b", "ξεκινάμε τη συνάντηση")]}]
    c["chapters"] = [{"title": "Τέλος", "quote": "ποιο μοντέλο"}, {"title": "Αρχή", "quote": "Καλημέρα σε όλους"}]
    return c


def link(*dec, dis=()):
    return {"decisions": [{"id": i, "replaces": r} for i, r in dec], "disagreements": [{"id": i, "settled_by": s} for i, s in dis]}


def test_brief_ids_and_order_are_assigned_by_code(mk_settings):
    chat = ScriptedChat(brief3(), LINK_NONE)
    r = genb(chat, mk_settings)
    c = r.content
    assert [(d["id"], d["text"]) for d in c["decisions"]] == [("d1", "Deploy 12/11"), ("d2", "Documentation"),
                                                              ("d3", "Μοντέλο"), ("d4", "Δεν ειπώθηκε")]
    assert [(d["id"], d["topic"]) for d in c["disagreements"]] == [("x1", "Early"), ("x2", "Late")]
    assert [ch["title"] for ch in c["chapters"]] == ["Αρχή", "Τέλος"]
    assert [d["verified"] for d in c["decisions"]] == [True, True, True, False]
    assert schemas.validate("brief", c, stored=True) == []
    assert "at_ms" not in str(c)                                    # times are added when read, never stored


def test_the_link_call_sees_numbered_items_with_times_and_no_transcript(mk_settings):
    chat = ScriptedChat(brief3(), LINK_NONE)
    r = genb(chat, mk_settings)
    assert [c["kind"] for c in chat.calls] == ["brief", "brief_link"]
    call = chat.calls[1]
    assert call["max_tokens"] == mk_settings().llm.max_tokens_brief_link == 800
    assert call["schema"]["properties"].keys() == {"decisions", "disagreements"}
    text = call["messages"][0]["content"]
    for needle in ("d1", "d4", "x1", "x2", "Deploy 12/11", "Early", "00:01"):
        assert needle in text, needle
    assert SEGS[0] not in text and SEGS[3] not in text              # no transcript in the link call
    assert r.content["links"] == "ok"


def test_link_is_applied_in_code_and_never_changes_text(mk_settings):
    reply = link(("d2", ["d1"]), ("d3", []), dis=[("x1", "d2"), ("x2", None)])
    r = genb(ScriptedChat(brief3(), reply), mk_settings)
    d = {x["id"]: x for x in r.content["decisions"]}
    assert (d["d2"]["status"], d["d2"]["replaces"], d["d2"]["replaced_by"]) == ("final", ["d1"], None)
    assert (d["d1"]["status"], d["d1"]["replaces"], d["d1"]["replaced_by"]) == ("replaced", [], "d2")
    assert (d["d3"]["status"], d["d4"]["status"]) == ("final", "final")
    assert [x["settled_by"] for x in r.content["disagreements"]] == ["d2", None]
    assert [x["text"] for x in r.content["decisions"]] == ["Deploy 12/11", "Documentation", "Μοντέλο", "Δεν ειπώθηκε"]
    assert r.content["links"] == "ok" and schemas.validate("brief", r.content, stored=True) == []


def test_link_is_folded_into_the_one_attempt_and_kept_in_params(mk_settings):
    r = genb(ScriptedChat(brief3(), link(("d2", ["d1"]))), mk_settings)
    assert len(r.attempts) == 1 and r.attempts[0].valid
    a = r.attempts[0]
    assert (a.tokens_in, a.tokens_out, a.wall_ms) == (200, 100, 1000)         # 100/50/500 per call, two calls
    lk = r.params["link"]
    assert lk["status"] == "ok" and "d1" in lk["prompt"] and '"d2"' in lk["reply"] and lk["errors"] is None
    assert r.stats["tokens_in"] == 200


@pytest.mark.parametrize("bad,needle", [
    (link(("d2", ["d9"])), "d9"),                                   # unknown id
    (link(("d9", [])), "d9"),
    (link(("d1", ["d2"])), "d2"),                                   # points forward
    (link(("d2", ["d2"])), "d2"),                                   # itself
    (link(("d2", ["d1"]), ("d3", ["d1"])), "d1"),                   # d1 replaced twice
    (link(dis=[("x1", "d9")]), "d9"),                               # settled by an unknown decision
    (link(dis=[("x9", None)]), "x9"),
    ({"decisions": "d1"}, "decisions"),                             # wrong shape
])
def test_each_invalid_link_is_retried_once_with_the_error(mk_settings, bad, needle):
    chat = ScriptedChat(brief3(), bad, link(("d2", ["d1"])))
    r = genb(chat, mk_settings)
    assert [c["kind"] for c in chat.calls] == ["brief", "brief_link", "brief_link"]
    retry = chat.calls[2]["messages"]
    assert len(retry) == 3 and retry[1]["role"] == "assistant" and needle in retry[-1]["content"]
    assert r.content["links"] == "ok" and r.params["link"]["attempts"] == 2
    assert r.attempts[0].tokens_in == 300                                      # all three calls counted


def test_a_link_that_stays_invalid_keeps_the_brief_with_links_failed(mk_settings):
    chat = ScriptedChat(brief3(), link(("d1", ["d2"])), link(("d1", ["d2"])))
    r = genb(chat, mk_settings)
    c = r.content
    assert r.attempts[0].valid and len(r.attempts) == 1 and len(chat.calls) == 3
    assert c["links"] == "failed" and [d["status"] for d in c["decisions"]] == ["final"] * 4
    assert all(d["replaces"] == [] and d["replaced_by"] is None for d in c["decisions"])
    assert all(x["settled_by"] is None for x in c["disagreements"])
    assert r.params["link"]["status"] == "failed" and r.params["link"]["errors"]
    assert schemas.validate("brief", c, stored=True) == []


def test_a_link_call_that_errors_keeps_the_brief_with_links_failed(mk_settings):
    r = genb(ScriptedChat(brief3(), LLMError("LM Studio died")), mk_settings)
    assert r.content["links"] == "failed" and r.attempts[0].valid
    assert "LM Studio died" in r.params["link"]["error"]


def test_a_cancel_during_the_link_call_propagates(mk_settings):
    with pytest.raises(StreamCancelled):
        genb(ScriptedChat(brief3(), StreamCancelled()), mk_settings)


def test_link_is_skipped_with_fewer_than_two_decisions(mk_settings):
    chat = ScriptedChat(good("brief"))
    r = genb(chat, mk_settings)
    assert len(chat.calls) == 1 and r.content["links"] == "skipped" and "link" not in r.params
    d = r.content["decisions"][0]
    assert (d["id"], d["status"], d["replaces"], d["replaced_by"]) == ("d1", "final", [], None)
    lesson = good("brief", kind_of_recording="lesson", decisions=[], disagreements=[], action_items=[])
    r = genb(ScriptedChat(lesson), mk_settings)
    assert r.content["links"] == "skipped" and r.content["decisions"] == []


def test_one_decision_and_one_disagreement_do_get_a_link_call(mk_settings):
    c = brief3()
    c["decisions"], c["disagreements"] = c["decisions"][:1], c["disagreements"][:1]
    chat = ScriptedChat(c, link(dis=[("x1", "d1")]))
    r = genb(chat, mk_settings)
    assert len(chat.calls) == 2 and r.content["disagreements"][0]["settled_by"] == "d1"


def test_two_decisions_and_no_disagreement_do_get_a_link_call_and_without_timed_segments_order_is_the_models(mk_settings):
    chat = ScriptedChat(brief3(), LINK_NONE)
    r = genb(chat, mk_settings, timed=None)
    assert [d["text"] for d in r.content["decisions"]] == ["Documentation", "Deploy 12/11", "Μοντέλο", "Δεν ειπώθηκε"]
    assert [d["id"] for d in r.content["decisions"]] == ["d1", "d2", "d3", "d4"]


def test_a_decision_from_chunk_3_can_replace_one_from_chunk_1_and_the_link_runs_once(mk_settings, tmp_path):
    def reply(call):
        if call["kind"] == "reduce":
            return {"text": "Τελική σύνοψη"}
        if call["kind"] == "brief_link":
            return link(("d2", ["d1"]))
        ids = sorted({int(w) for w in re.findall(r"Segment (\d+)", call["messages"][0]["content"])})
        c = good("brief")
        c["decisions"] = [{"text": f"Deploy {i}", "quote": f"θα κάνουμε deploy στις {i} Νοεμβρίου"} for i in ids if i in (0, 5)]
        c["chapters"], c["action_items"], c["open_questions"] = [], [], []
        return c
    chat = ScriptedChat(default=reply)
    r = genb(chat, mk_settings, segments=LONG, data_dir=tmp_path, **ENV)
    assert [c["kind"] for c in chat.calls] == ["brief"] * 3 + ["reduce", "brief_link"]
    d = r.content["decisions"]
    assert [(x["id"], x["text"], x["status"]) for x in d] == [("d1", "Deploy 0", "replaced"), ("d2", "Deploy 5", "final")]
    assert d[1]["replaces"] == ["d1"] and d[0]["replaced_by"] == "d2"
    assert r.content["summary"] == "Τελική σύνοψη" and r.mode == "map-reduce" and r.chunks == 3
    assert r.attempts[0].tokens_in == 500


def test_merge_brief_concatenates_dedupes_and_votes_on_the_kind():
    def c(kind, dec, topic, stance, chapters):
        b = good("brief", kind_of_recording=kind, decisions=dec, chapters=chapters)
        b["disagreements"] = [{"topic": topic, "positions": [{"who": "A", "stance": stance, "quote": "q"},
                                                            {"who": "B", "stance": "b", "quote": "q"}]}]
        return b
    d = lambda t: {"text": t, "quote": "q"}
    ch = lambda t: {"title": t, "quote": "q"}
    m = pipeline.merge("brief", [c("meeting", [d("Α"), d("Β")], "Budget", "x", [ch("Intro"), ch("Plan")]),
                                 c("lesson", [d("β"), d("Γ")], "budget", "y", [ch("Plan"), ch("Plan 2")]),
                                 c("meeting", [d("Δ")], "Other", "z", [ch("Plan 2")])])
    assert m["kind_of_recording"] == "meeting"
    assert [x["text"] for x in m["decisions"]] == ["Α", "Β", "Γ", "Δ"]
    assert [x["topic"] for x in m["disagreements"]] == ["Budget", "Other"]
    assert [p["stance"] for p in m["disagreements"][0]["positions"]] == ["x", "b", "y"]       # merged, duplicates once
    assert [x["title"] for x in m["chapters"]] == ["Intro", "Plan", "Plan 2"]                # consecutive repeats dropped
    tie = pipeline.merge("brief", [c("lesson", [], "t", "s", []), c("meeting", [], "t", "s", [])])
    assert tie["kind_of_recording"] == "lesson"                                              # ties: first


# ---- brief: code enforces what the prompt only asked (names, not-a-meeting, unsupported positions) -----------------

def pos(who, stance, quote):
    return {"who": who, "stance": stance, "quote": quote}


def test_a_lesson_loses_decisions_disagreements_and_action_items_and_the_counts_are_kept(mk_settings):
    c = brief3()
    c["kind_of_recording"] = "lesson"
    chat = ScriptedChat(c)
    r = genb(chat, mk_settings)
    got = r.content
    assert (got["decisions"], got["disagreements"], got["action_items"]) == ([], [], [])
    assert got["dropped_not_meeting"] == {"decisions": 4, "disagreements": 2, "action_items": 1}
    assert got["links"] == "skipped" and len(chat.calls) == 1               # nothing left to link
    assert len(got["open_questions"]) == 1 and schemas.validate("brief", got, stored=True) == []


def test_a_meeting_has_no_not_meeting_counts(mk_settings):
    assert "dropped_not_meeting" not in genb(ScriptedChat(good("brief")), mk_settings).content


def test_names_are_kept_only_when_spoken_in_the_transcript(mk_settings):
    c = good("brief")                                                         # owner "Νίκος" is in SEGS
    c["action_items"].append({"text": "x", "owner": "Γιώργος", "due": None, "done": False, "quote": "Θα κάνουμε deploy"})
    c["disagreements"] = [{"topic": "t", "positions": [pos("Speaker 1", "a", "Θα κάνουμε deploy"),
                                                       pos("Νίκος", "b", "ετοιμάσει το documentation")]}]
    got = genb(ScriptedChat(c), mk_settings).content
    assert [a["owner"] for a in got["action_items"]] == ["Νίκος", None]
    assert [p["who"] for p in got["disagreements"][0]["positions"]] == [None, "Νίκος"]
    assert got["names_cleared"] == 2


def test_a_speaker_name_of_a_labelled_run_is_kept(mk_settings):
    segs = ["Αλέξης: Θα κάνουμε deploy στις 12 Νοεμβρίου.", "Μαρία: Συμφωνώ, ετοιμάζω το documentation."]
    c = good("brief")
    c["action_items"] = [{"text": "x", "owner": "Μαρία", "due": None, "done": False, "quote": "ετοιμάζω το documentation"}]
    got = genb(ScriptedChat(c), mk_settings, segments=segs, timed=None).content
    assert got["action_items"][0]["owner"] == "Μαρία" and "names_cleared" not in got


def test_a_disagreement_keeps_only_positions_whose_quote_was_found(mk_settings):
    c = good("brief")
    c["disagreements"] = [
        {"topic": "one side only", "positions": [pos(None, "a", "Θα κάνουμε deploy"), pos(None, "b", "δεν ειπώθηκε ποτέ")]},
        {"topic": "three", "positions": [pos(None, "a", "Θα κάνουμε deploy"), pos(None, "b", "δεν ειπώθηκε ποτέ"),
                                         pos(None, "c", "ποιο μοντέλο")]}]
    got = genb(ScriptedChat(c, LINK_NONE), mk_settings).content
    assert [x["topic"] for x in got["disagreements"]] == ["three"]
    assert [p["stance"] for p in got["disagreements"][0]["positions"]] == ["a", "c"]
    assert got["dropped_unsupported"] == 1 and schemas.validate("brief", got, stored=True) == []


def test_enforcement_runs_once_on_the_merged_brief(mk_settings, tmp_path):
    def reply(call):
        if call["kind"] == "reduce":
            return {"text": "σύνοψη"}
        c = good("brief", kind_of_recording="lesson")
        c["decisions"] = [{"text": "Deploy", "quote": "θα κάνουμε deploy στις 1 Νοεμβρίου"}]
        return c
    chat = ScriptedChat(default=reply)
    r = genb(chat, mk_settings, segments=LONG, data_dir=tmp_path, **ENV)
    assert r.content["decisions"] == [] and r.content["dropped_not_meeting"]["decisions"] == 1
    assert [c["kind"] for c in chat.calls] == ["brief"] * 3 + ["reduce"]
