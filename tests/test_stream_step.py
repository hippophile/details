"""llm-streaming: the analyse step with streaming on, on the real runner with FakeLMStudio (HTTP mode, SSE)."""
import asyncio, json, time

import pytest

from details.llm import pipeline, schemas
from details.llm.lmstudio_ctl import identifier_for
from details.llm.stream_hub import StreamHub
from details.runner import steps_analyse
from fakes.lmstudio import FakeLMStudio
from jobs_helpers import wait_status, wait_until
from llm_helpers import FULL, ScriptedChat, analysis_env, good, good_responder, seed_run, sync_prompts, TEMPLATE, SEGS
from test_llm_steps import KINDS, go, runs, start

SECRET = "password=SeCr3tPw"


def jtext(kind, **over):
    return json.dumps(good(kind, **over), ensure_ascii=False)


def log_lines(e, jid):
    f = e.data_dir / "jobs" / jid / "analyse" / "log.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.is_file() else []


def bound(e, hub):
    steps_analyse.bind(e.pool, memory=e.memory, settings=e.settings, hub=hub, lease=e.runner.lease)


def test_streaming_is_the_default_and_all_kinds_match_the_plain_structure(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            bound(e, StreamHub())
            gid, jid = await start(e, run_id)
            await wait_status(e.svc, jid, "done")
            return gid, jid, log_lines(e, jid)
    gid, jid, lines = go(body)
    assert all(r.get("stream") is True for r in fake.requests) and len(fake.requests) == len(KINDS)
    rows = runs(db, gid)
    assert [(r[0], r[1], r[2]) for r in rows] == [(k, "done", True) for k in KINDS]
    out = {r[0]: r[1] for r in db.execute("SELECT kind, content FROM output").fetchall()}
    for k in KINDS:                                              # same content as the plain path would store
        assert schemas.validate(k, out[k], stored=True) == [] and out[k]["schema_version"] == 1
    calls = [l for l in lines if l["msg"] == "LM Studio call"]
    assert len(calls) == len(KINDS) and all(l["data"]["stream"] is True and l["data"]["ttft_ms"] is not None for l in calls)
    assert all(l["data"].get("fallback") in (None, "") for l in calls)
    raw = rows[0][8]
    assert raw["response"]["stream"] is True and raw["text"]


def test_stream_setting_off_sends_no_stream_request(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def body():
        async with analysis_env(db_url, mk_settings, fake, DETAILS_LLM_STREAM="false") as e:
            bound(e, StreamHub())
            gid, jid = await start(e, run_id, ["tags"])
            await wait_status(e.svc, jid, "done")
            return jid, log_lines(e, jid)
    jid, lines = go(body)
    assert [r["stream"] for r in fake.requests] == [False] and "stream_options" not in fake.requests[0]
    call = [l for l in lines if l["msg"] == "LM Studio call"][0]
    assert call["data"]["stream"] is False


def test_invalid_then_valid_stream_makes_two_rows_linked_by_retry(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio().script("this is not json", jtext("tags"))
    hub, seen = StreamHub(), []

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            bound(e, hub)
            gid, jid = await start(e, run_id, ["tags"])
            task = asyncio.create_task(_watch(hub, jid, seen))
            await wait_status(e.svc, jid, "done")
            task.cancel()
            return gid
    gid = go(body)
    rows = runs(db, gid)
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [("tags", "done", False, False), ("tags", "done", True, True)]
    assert db.execute("SELECT count(*) FROM output").fetchone()[0] == 1
    assert ("attempt_start", 1) in seen and ("attempt_start", 2) in seen


async def _watch(hub, jid, seen):
    seq = 0
    while True:
        await hub.wait(jid, seq, 0.05)
        evs = hub.events_since(jid, seq)
        if evs is None:
            evs = []
            seq = hub.snapshot(jid)["seq"]
        for s, typ, data in evs:
            seq = s
            if typ == "attempt_start":
                seen.append((typ, data["attempt"]))
            if typ == "attempt_end":
                seen.append((typ, data["valid"]))


def test_hub_sees_attempts_deltas_and_validity(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio().script("not json", jtext("tags"))
    hub, seen = StreamHub(), []

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            bound(e, hub)
            gid, jid = await start(e, run_id, ["tags"])
            task = asyncio.create_task(_watch(hub, jid, seen))
            await wait_status(e.svc, jid, "done")
            await asyncio.sleep(0.2)
            task.cancel()
            return jid
    jid = go(body)
    assert seen == [("attempt_start", 1), ("attempt_end", False), ("attempt_start", 2), ("attempt_end", True)]
    assert hub.snapshot(jid)["text"] == ""                       # dropped when the step ends


def test_unverified_quote_is_still_flagged(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    bad = good("tags")
    bad["tags"][0]["quote"] = "this was never said in the meeting"
    fake = FakeLMStudio().script(json.dumps(bad))

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            bound(e, StreamHub())
            gid, jid = await start(e, run_id, ["tags"])
            await wait_status(e.svc, jid, "done")
    go(body)
    content = db.execute("SELECT content FROM output").fetchone()[0]
    assert content["tags"][0]["verified"] is False and content["tags"][1]["verified"] is True


def test_map_reduce_chunks_become_attempts(db, db_url, mk_settings):
    sync_prompts(db_url)
    segs = [f"Segment {i} " + "λέξη " * 400 for i in range(6)]
    _, run_id = seed_run(db, mk_settings(db_url), segs=segs)
    fake = FakeLMStudio()
    fake.responder = lambda body: {"text": "Σύνοψη"} if pipeline_kind(body) == "reduce" else good(pipeline_kind(body))
    hub, seen = StreamHub(), []

    async def body():
        async with analysis_env(db_url, mk_settings, fake, DETAILS_LLM_SINGLE_PASS_MAX_TOKENS="500",
                                DETAILS_LLM_CHUNK_TOKENS="1500") as e:
            bound(e, hub)
            gid, jid = await start(e, run_id, ["notes"])
            task = asyncio.create_task(_chunks(hub, jid, seen))
            await wait_status(e.svc, jid, "done", timeout=30)
            await asyncio.sleep(0.2)
            task.cancel()
    go(body)
    starts = [x for x in seen if x[0] == "attempt_start"]
    assert len(starts) >= 3 and starts[0][2] is not None and starts[0][2][1] >= 2     # chunk (i, n)
    assert starts[-1][1] == "reduce"


def pipeline_kind(body):
    return body["response_format"]["json_schema"]["name"]


async def _chunks(hub, jid, seen):
    seq = 0
    while True:
        await hub.wait(jid, seq, 0.05)
        evs = hub.events_since(jid, seq)
        if evs is None:
            evs = []
            seq = hub.snapshot(jid)["seq"]
        for s, typ, data in evs:
            seq = s
            if typ == "attempt_start":
                seen.append((typ, data["kind"], data["chunk"]))


def test_400_on_stream_falls_back_to_one_plain_call_and_is_remembered(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio().script({"status": 400, "error": "stream is not supported"})
    fake.responder = lambda body: good(pipeline_kind(body))

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            bound(e, StreamHub())
            gid, jid = await start(e, run_id, ["checklist", "tags"])
            await wait_status(e.svc, jid, "done")
            return gid, log_lines(e, jid)
    gid, lines = go(body)
    assert [r["stream"] for r in fake.requests] == [True, False, False]       # 1 rejected, 1 plain, then no flapping
    assert {r[1] for r in runs(db, gid)} == {"done"} and {r[2] for r in runs(db, gid)} == {True}
    calls = [l["data"] for l in lines if l["msg"] == "LM Studio call"]
    assert calls[0]["stream"] is False and "400" in calls[0]["fallback"]
    assert calls[1]["stream"] is False and calls[1]["fallback"] in (None, "")


def test_drop_after_first_delta_fails_without_a_second_request(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio().script({"content": jtext("tags"), "stream": {"drop_after": 3}}, jtext("tags"))

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            bound(e, StreamHub())
            gid, jid = await start(e, run_id, ["tags"])
            job = await wait_status(e.svc, jid, "failed")
            return gid, job
    gid, job = go(body)
    assert len(fake.requests) == 1 and "stream ended early" in job["error"]
    assert [r[1] for r in runs(db, gid)] == ["failed"]


def test_cancel_mid_stream_stops_within_a_second_and_stores_nothing(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio().script({"content": jtext("checklist"), "stream": {"delay": 0.15}}, jtext("tags"))
    hub, t = StreamHub(), {}

    async def body():
        async with analysis_env(db_url, mk_settings, fake, grace="5") as e:
            bound(e, hub)
            gid, jid = await start(e, run_id, ["checklist", "tags"])
            await wait_until(lambda: hub.snapshot(jid)["pieces"] >= 3, what="3 deltas")
            t["cancel"] = time.monotonic()
            await e.svc.cancel(jid)
            await asyncio.wait_for(fake.disconnected.wait(), 3)
            await wait_status(e.svc, jid, "cancelled")
            t["done"] = time.monotonic()
            await asyncio.sleep(0.5)
            return gid, jid, await e.svc.get(jid)
    gid, jid, job = go(body)
    assert fake.disconnect_at - t["cancel"] < 1.0 and t["done"] - t["cancel"] < 2.5
    rows = runs(db, gid)
    assert [r[1] for r in rows] == ["cancelled", "cancelled"] and all(r[4] == "cancelled" for r in rows)
    assert db.execute("SELECT count(*) FROM output").fetchone()[0] == 0
    stored = db.execute("SELECT count(*) FROM analysis_run WHERE raw_response IS NOT NULL AND group_id=%s", (gid,)).fetchone()[0]
    assert stored == 0 and len(fake.requests) == 1 and job["status"] == "cancelled"


def test_pipeline_on_event_defaults_to_none_and_reports_validity_and_chunks():
    chat = ScriptedChat("not json", good("tags"))
    events = []
    from llm_helpers import TEMPLATE as T
    from details.settings import REPO_ROOT
    import types
    out = asyncio.run(pipeline.call_validated(chat, "tags", [{"role": "user", "content": "x"}],
                                              schemas.model_schema("tags"), lambda d: schemas.validate("tags", d), 400,
                                              on_event=lambda *a, **kw: events.append((a, kw))))
    assert [a.valid for a in out] == [False, True]
    assert [(a[0], kw["valid"], kw["attempt"]) for a, kw in events] == [("attempt_end", False, 1), ("attempt_end", True, 2)]
    # default None: the old call shape still works
    chat2 = ScriptedChat(good("tags"))
    out2 = asyncio.run(pipeline.call_validated(chat2, "tags", [{"role": "user", "content": "x"}],
                                               schemas.model_schema("tags"), lambda d: schemas.validate("tags", d), 400))
    assert out2[0].valid


def test_generate_kind_reports_chunk_positions():
    from details.settings import REPO_ROOT
    from conftest import make_settings
    import tempfile, pathlib
    s = make_settings(pathlib.Path(tempfile.mkdtemp()) / "d", DETAILS_LLM_SINGLE_PASS_MAX_TOKENS="500",
                      DETAILS_LLM_CHUNK_TOKENS="1500")
    segs = [f"Segment {i} " + "λέξη " * 400 for i in range(6)]
    chat = ScriptedChat(default=lambda c: {"text": "x"} if c["kind"] == "reduce" else good(c["kind"]))
    events = []
    r = asyncio.run(pipeline.generate_kind(chat, settings=s, kind="notes", template=TEMPLATE, model="m", context=8192,
                                           segments=segs, language="Greek", title="t", data_dir=None,
                                           on_event=lambda *a, **kw: events.append(a)))
    chunk_events = [a for a in events if a[0] == "chunk"]
    assert r.mode == "map-reduce" and chunk_events[0][1] == 1 and chunk_events[0][2] == r.chunks
    assert chunk_events[-1] == ("chunk", None, None)             # the reduce call has no chunk position


def test_secret_in_a_stream_error_chunk_never_reaches_log_snapshot_or_errors(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio().script({"content": jtext("tags"), "stream": {"error": f"model crashed {SECRET}"}})
    hub = StreamHub()

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            bound(e, hub)
            gid, jid = await start(e, run_id, ["tags"])
            job = await wait_status(e.svc, jid, "failed")
            from conftest import client_for
            from details.app import create_app
            raw_log = (e.data_dir / "jobs" / jid / "analyse" / "log.jsonl").read_text()
            return gid, jid, job, raw_log, hub.snapshot(jid)
    gid, jid, job, raw_log, snap = go(body)
    assert "model crashed" in job["error"]
    blob = json.dumps([job["error"], raw_log, snap, [list(r[:5]) for r in runs(db, gid)]])
    assert "SeCr3tPw" not in blob


def test_log_has_no_delta_or_prompt_text(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            bound(e, StreamHub())
            gid, jid = await start(e, run_id, ["debrief", "tags"])
            await wait_status(e.svc, jid, "done")
            files = list((e.data_dir / "jobs" / jid).rglob("log.jsonl"))
            return "\n".join(f.read_text() for f in files)
    text = go(body)
    for needle in ("Θα κάνουμε deploy", "documentation", "GDPR", "Καλημέρα", "Σύνοψη", "Deploy στις", "decisions"):
        assert needle not in text, needle
    assert '"ttft_ms"' in text
