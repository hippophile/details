"""run-insights C: GET /api/analysis-runs/{run_id}/inspect (retry chain, honest labels, failed call, 404)."""
import json

from fakes.lmstudio import FakeLMStudio
from llm_helpers import good, good_responder, seed_run, sync_prompts
from test_llm_api import api, finish, post

UNKNOWN = "00000000-0000-0000-0000-000000000000"


def test_retry_chain_returns_both_attempts_in_order(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.script("this is not json at all", good("tags"))

    async def body(c, s):
        r = await (await post(c, run_id, kinds=["tags"])).json()
        g = await finish(c, r["group_id"])
        k = g["kinds"][0]
        assert k["attempts"] == 2 and g["status"] == "done"
        i = await c.get(f"/api/analysis-runs/{k['run_id']}/inspect")
        assert i.status == 200
        d = await i.json()
        a1, a2 = d["attempts"]
        assert [a1["attempt"], a2["attempt"]] == [1, 2] and a1["retry_of"] is None and a2["retry_of"] == a1["run_id"]
        assert a1["raw_text"] == "this is not json at all" and a1["schema_valid"] is False and a1["validation_errors"]
        assert a2["schema_valid"] is True and json.loads(a2["raw_text"]) == good("tags")
        assert "Καλημέρα" in a1["rendered_prompt"] and "this is not json at all" in a2["rendered_prompt"]
        assert a1["rendered_prompt"] != a2["rendered_prompt"]
        for a in (a1, a2):
            assert a["tokens_in"] > 0 and a["tokens_out"] > 0 and a["wall_ms"] is not None and a["finish_reason"] == "stop"
        assert d["kind"] == "tags" and d["model_id"] == "gemma-cheap" and d["output_language"] == "transcript"
        assert d["template"]["name"] and d["template"]["version"] >= 1 and len(d["template"]["sha256"]) == 64
        assert d["params"]["max_tokens"] and d["params"]["temperature"] == 0.1
        assert d["prompt_scope"] == "full" and d["raw_text_kind"] == "model_reply"
        assert d["endpoint"].startswith("http://127.0.0.1")
        # any attempt id returns the same chain
        d1 = await (await c.get(f"/api/analysis-runs/{a1['run_id']}/inspect")).json()
        assert [a["run_id"] for a in d1["attempts"]] == [a1["run_id"], a2["run_id"]]
    api(db_url, mk_settings, body, fake=fake)


def test_map_reduce_runs_carry_the_two_honest_labels(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def body(c, s):
        r = await (await post(c, run_id, kinds=["tags"])).json()
        g = await finish(c, r["group_id"])
        db.execute("UPDATE analysis_run SET params = params || '{\"mode\": \"map-reduce\"}'::jsonb WHERE group_id=%s",
                   (r["group_id"],))
        d = await (await c.get(f"/api/analysis-runs/{g['kinds'][0]['run_id']}/inspect")).json()
        assert d["prompt_scope"] == "first_chunk" and d["raw_text_kind"] == "merged_by_code"
    api(db_url, mk_settings, body, fake=fake)


def test_call_that_failed_before_a_reply_has_null_prompt_and_error(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.script({"status": 500, "error": "model crashed"})

    async def body(c, s):
        r = await (await post(c, run_id, kinds=["tags"])).json()
        g = await finish(c, r["group_id"])
        d = await (await c.get(f"/api/analysis-runs/{g['kinds'][0]['run_id']}/inspect")).json()
        a, = d["attempts"]
        assert a["status"] == "failed" and a["rendered_prompt"] is None and a["raw_text"] is None
        assert "500" in a["error"]
    api(db_url, mk_settings, body, fake=fake)


def test_unknown_or_malformed_run_is_404(db, db_url, mk_settings):
    async def body(c, s):
        assert (await c.get(f"/api/analysis-runs/{UNKNOWN}/inspect")).status == 404
        assert (await c.get("/api/analysis-runs/nope/inspect")).status == 404
    api(db_url, mk_settings, body)
