"""llm-outputs: analysis HTTP API on the real app with FakeLMStudio (transport + HTTP mode)."""
import asyncio

import pytest

from conftest import client_for
from details.app import create_app
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from jobs_helpers import wait_until
from llm_helpers import SEGS, good, good_responder, seed_run, sync_prompts
from stt_helpers import insert_run, insert_segments


def api(db_url, mk_settings, body, fake=None, **env):
    fake = fake or FakeLMStudio()
    fake.responder = fake.responder or good_responder

    async def go():
        url = await fake.start_http()
        s = mk_settings(db_url, DETAILS_LMSTUDIO_URL=url, DETAILS_RUNNER_CANCEL_GRACE_S="1", **env)
        try:
            async with client_for(create_app(s, lm_transport=fake, meminfo=FakeMemInfo())) as c:
                await body(c, s)
        finally:
            await fake.stop_http()
    asyncio.run(asyncio.wait_for(go(), 40))


async def post(c, run_id, **body):
    body = {"model_id": "gemma-cheap", "kinds": ["debrief", "checklist"], "output_language": "transcript", **body}
    return await c.post(f"/api/stt-runs/{run_id}/analyses", json=body)


async def finish(c, gid):
    async def done():
        return (await (await c.get(f"/api/analyses/{gid}")).json())["status"] in ("done", "failed", "cancelled")
    await wait_until(done, what="analysis finished")
    return await (await c.get(f"/api/analyses/{gid}")).json()


def test_start_202_then_get_lists_outputs_with_timings(db, db_url, mk_settings):
    sync_prompts(db_url)
    rid, run_id = seed_run(db, mk_settings(db_url))

    async def body(c, s):
        r = await post(c, run_id)
        assert r.status == 202
        j = await r.json()
        assert j["group_id"] and j["job_id"]
        g = await finish(c, j["group_id"])
        assert g["status"] == "done" and g["model_id"] == "gemma-cheap" and g["stt_run_id"] == run_id
        assert [k["kind"] for k in g["kinds"]] == ["debrief", "checklist"]
        k = g["kinds"][1]
        assert k["schema_valid"] is True and k["output"]["content"]["items"][0]["verified"] is True
        assert k["wall_ms"] >= 0 and k["tokens_in"] > 0 and k["tokens_out"] > 0 and k["output"]["id"]
        assert g["tokens_in"] > 0 and g["verified"] >= 1 and g["unverified"] == 0 and g["n"] == 1
        lst = await (await c.get(f"/api/stt-runs/{run_id}/analyses")).json()
        assert [x["group_id"] for x in lst] == [j["group_id"]] and "gemma" in lst[0]["label"].lower()
        r2 = await post(c, run_id, kinds=["tags"])
        assert (await r2.json())["group_id"] != j["group_id"]
        await finish(c, (await r2.json())["group_id"])
        lst = await (await c.get(f"/api/stt-runs/{run_id}/analyses")).json()
        assert [x["n"] for x in lst] == [2, 1]                      # newest first
    api(db_url, mk_settings, body)


def test_422s_enqueue_nothing(db, db_url, mk_settings):
    sync_prompts(db_url)
    s0 = mk_settings(db_url)
    _, run_id = seed_run(db, s0)
    _, running = seed_run(db, s0, status="running")

    async def body(c, s):
        cases = [(run_id, {"kinds": ["diagram"]}), (run_id, {"kinds": ["speakers"]}), (run_id, {"kinds": []}),
                 (run_id, {"kinds": "debrief"}), (run_id, {"model_id": "whisper-large"}),
                 (run_id, {"model_id": "nope"}), (run_id, {"model_id": None}),
                 (run_id, {"output_language": "fr"}), (running, {})]
        for rid, over in cases:
            r = await post(c, rid, **over)
            assert r.status == 422, over
            assert "error" in await r.json()
        assert (await post(c, "00000000-0000-0000-0000-000000000000")).status == 404
        assert (await post(c, "not-a-uuid")).status == 404
    api(db_url, mk_settings, body)
    assert db.execute("SELECT count(*) FROM job").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0


def test_invalid_output_returns_raw_text(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio().script("{oops", "{still oops")

    async def body(c, s):
        g = await finish(c, (await (await post(c, run_id, kinds=["tags"])).json())["group_id"])
        k = g["kinds"][0]
        assert g["status"] == "done" and k["schema_valid"] is False and k["output"] is None
        assert k["raw_text"] == "{still oops" and k["attempts"] == 2 and k["validation_errors"]
    api(db_url, mk_settings, body, fake)


async def checklist_output(c, run_id):
    g = await finish(c, (await (await post(c, run_id, kinds=["checklist", "tags"])).json())["group_id"])
    return g["group_id"], {k["kind"]: k["output"]["id"] for k in g["kinds"]}


def test_patch_tick_persists_and_leaves_other_items(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))

    async def body(c, s):
        gid, ids = await checklist_output(c, run_id)
        oid = ids["checklist"]
        r = await c.patch(f"/api/outputs/{oid}/items/1", json={"done": True})
        assert r.status == 200
        g = await (await c.get(f"/api/analyses/{gid}")).json()
        items = [k for k in g["kinds"] if k["kind"] == "checklist"][0]["output"]["content"]["items"]
        assert [i["done"] for i in items] == [False, True, False]
        assert (await c.patch(f"/api/outputs/{oid}/items/1", json={"done": False})).status == 200
        g = await (await c.get(f"/api/analyses/{gid}")).json()
        assert [i["done"] for i in [k for k in g["kinds"] if k["kind"] == "checklist"][0]["output"]["content"]["items"]] == [False] * 3
    api(db_url, mk_settings, body)


def test_patch_errors(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))

    async def body(c, s):
        gid, ids = await checklist_output(c, run_id)
        oid = ids["checklist"]
        before = await (await c.get(f"/api/analyses/{gid}")).json()
        assert (await c.patch(f"/api/outputs/{oid}/items/7", json={"done": True})).status == 404
        assert (await c.patch(f"/api/outputs/{oid}/items/-1", json={"done": True})).status == 404
        assert (await c.patch(f"/api/outputs/{oid}/items/x", json={"done": True})).status == 404
        for bad in ({"done": "yes"}, {"done": 1}, {}, [1]):
            assert (await c.patch(f"/api/outputs/{oid}/items/0", json=bad)).status == 422, bad
        assert (await c.patch(f"/api/outputs/{ids['tags']}/items/0", json={"done": True})).status == 409
        assert (await c.patch("/api/outputs/00000000-0000-0000-0000-000000000000/items/0",
                              json={"done": True})).status == 404
        assert await (await c.get(f"/api/analyses/{gid}")).json() == before
    api(db_url, mk_settings, body)


def test_markdown_endpoints_golden_headers_and_determinism(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))

    async def body(c, s):
        gid, ids = await checklist_output(c, run_id)
        await c.patch(f"/api/outputs/{ids['checklist']}/items/0", json={"done": True})
        r = await c.get(f"/api/outputs/{ids['checklist']}/markdown")
        assert r.status == 200 and r.headers["Content-Type"].startswith("text/markdown")
        assert "Content-Disposition" not in r.headers
        text = await r.text()
        assert text.splitlines()[2].startswith("- [x] Deploy") and "- [ ] Documentation" in text
        assert (await (await c.get(f"/api/outputs/{ids['checklist']}/markdown")).read()) == text.encode()
        d = await c.get(f"/api/outputs/{ids['checklist']}/markdown?download=1")
        assert d.headers["Content-Disposition"].startswith("attachment") and ".md" in d.headers["Content-Disposition"]
        grp = await c.get(f"/api/analyses/{gid}/markdown?download=1")
        assert grp.headers["Content-Disposition"].rstrip('"').endswith(".md")
        gtext = await grp.text()
        assert gtext.index("# Ενέργειες") < gtext.index("# Tags")
        assert (await c.get("/api/outputs/00000000-0000-0000-0000-000000000000/markdown")).status == 404
        assert (await c.get("/api/analyses/00000000-0000-0000-0000-000000000000/markdown")).status == 404
        assert (await c.get("/api/analyses/00000000-0000-0000-0000-000000000000")).status == 404
    api(db_url, mk_settings, body)


def test_get_group_adds_at_ms_to_found_quotes_only_and_list_has_no_content(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))

    def responder(body):
        c = good_responder(body)
        if "items" in c:
            c["items"][2]["quote"] = "Θα απολύσουμε τον Νίκο"
        return c
    fake = FakeLMStudio()
    fake.responder = responder

    async def body(c, s):
        g = await finish(c, (await (await post(c, run_id)).json())["group_id"])
        deb, ck = (k["output"]["content"] for k in g["kinds"])
        assert deb["decisions"][0]["at_ms"] == 1000 and deb["risks"][0]["at_ms"] == 3000
        assert [i.get("at_ms") for i in ck["items"]] == [1000, 2000, None]
        assert ck["items"][2]["verified"] is False
        lst = await (await c.get(f"/api/stt-runs/{run_id}/analyses")).json()
        assert "kinds" not in lst[0]
        text = await (await c.get(f"/api/analyses/{g['group_id']}/markdown")).text()
        assert "  > “Θα κάνουμε deploy στις 12 Νοεμβρίου” [00:01]" in text and "quote not found" in text
        oid = g["kinds"][1]["output"]["id"]
        assert "[00:02]" in await (await c.get(f"/api/outputs/{oid}/markdown")).text()
    api(db_url, mk_settings, body, fake)


def test_numbers_are_per_recording_across_transcript_runs(db, db_url, mk_settings):
    sync_prompts(db_url)
    rid, run1 = seed_run(db, mk_settings(db_url))
    run2 = insert_run(db, rid, status="done", language="el", text="x")
    insert_segments(db, run2, 2)
    _, other = seed_run(db, mk_settings(db_url))

    async def body(c, s):
        for rr in (run1, run2, other):
            await finish(c, (await (await post(c, rr, kinds=["tags"])).json())["group_id"])
        ls = [(await (await c.get(f"/api/stt-runs/{rr}/analyses")).json())[0] for rr in (run1, run2, other)]
        assert [x["n"] for x in ls] == [1, 2, 1]
        assert [x["label"].split(" · ")[0] for x in ls] == ["Analysis #1", "Analysis #2", "Analysis #1"]
        g2 = await (await c.get(f"/api/analyses/{ls[1]['group_id']}")).json()
        assert g2["n"] == 2 and g2["label"].startswith("Analysis #2")
    api(db_url, mk_settings, body)


def test_group_markdown_of_a_failed_analysis_is_409_with_the_error(db, db_url, mk_settings):
    _, run_id = seed_run(db, mk_settings(db_url))
    gids = {}
    for st, err in (("failed", "needs ~17 GB, 13.3 GB free"), ("queued", None)):
        gids[st] = db.execute("INSERT INTO analysis_run (group_id, stt_run_id, kind, model_id, status, error) "
                              "VALUES (gen_random_uuid(), %s, 'tags', 'gemma-cheap', %s, %s) RETURNING group_id",
                              (run_id, st, err)).fetchone()[0]

    async def body(c, s):
        r = await c.get(f"/api/analyses/{gids['failed']}/markdown")
        assert r.status == 409 and (await r.json())["error"] == "needs ~17 GB, 13.3 GB free"
        r = await c.get(f"/api/analyses/{gids['queued']}/markdown")
        assert r.status == 404 and (await r.json())["error"] == "this analysis has no outputs yet"
    api(db_url, mk_settings, body)
