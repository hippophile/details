"""llm-outputs: the analyse step + terminal finalizer on the real runner, FakeLMStudio in HTTP mode."""
import asyncio, hashlib

import pytest

from details.api import analyses
from details.llm.lmstudio_ctl import identifier_for
from fakes.lmstudio import FakeLMStudio
from jobs_helpers import wait_status, wait_until
from llm_helpers import FULL, SEGS, analysis_env, good, good_responder, seed_run, sync_prompts

KINDS = ["brief", "debrief", "notes", "checklist", "tags"]


def go(fn, timeout=40):
    return asyncio.run(asyncio.wait_for(fn(), timeout))


async def start(e, run_id, kinds=KINDS, lang="transcript", model="gemma-cheap"):
    return await analyses.start_analysis(e.pool, e.svc, e.models, e.settings, run_id, model, kinds, lang)


def runs(db, gid):
    return db.execute("SELECT kind, status, schema_valid, retry_of IS NOT NULL, error, tokens_in, tokens_out, wall_ms, "
                      "raw_response, rendered_prompt, input_text_sha256, model_id, group_id::text "
                      "FROM analysis_run WHERE group_id=%s ORDER BY created_at, id", (gid,)).fetchall()


def test_job_steps_are_lm_load_analyse_lm_unload(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))

    async def body():
        async with analysis_env(db_url, mk_settings, FakeLMStudio(), start=False) as e:
            gid, jid = await start(e, run_id, ["debrief", "tags"])
            job = await e.svc.get(jid)
            return gid, job
    gid, job = go(body)
    assert [s["name"] for s in job["steps"]] == ["lm_load", "analyse", "lm_unload"]
    assert job["steps"][0]["params"] == {"model_id": "gemma-cheap"}
    assert job["steps"][1]["params"]["group_id"] == gid and job["kind"] == "analyse"
    assert [r[0] for r in runs(db, gid)] == ["debrief", "tags"] and {r[1] for r in runs(db, gid)} == {"queued"}


def test_end_to_end_all_kinds_with_load_and_unload(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            gid, jid = await start(e, run_id)
            await wait_status(e.svc, jid, "done")
            return gid
    gid = go(body)
    rows = runs(db, gid)
    assert [(r[0], r[1], r[2]) for r in rows] == [(k, "done", True) for k in KINDS]
    assert db.execute("SELECT count(*) FROM output").fetchone()[0] == 5
    assert [c[0] for c in fake.calls] == ["load", "unload"] and fake.calls[0][2] == identifier_for("gemma-cheap")
    assert len(fake.requests) == 5 and {r["model"] for r in fake.requests} == {identifier_for("gemma-cheap")}
    r = rows[0]
    assert r[5] > 0 and r[6] > 0 and r[7] >= 0 and r[8]["text"] and "Greek" in r[9]
    assert r[10] == hashlib.sha256("\n".join(SEGS).encode()).hexdigest() and r[11] == "gemma-cheap"
    q = db.execute("SELECT content->'items'->0->>'verified' FROM output WHERE kind='checklist'").fetchone()[0]
    assert q == "true"
    assert all(db.execute("SELECT tok_per_s FROM analysis_run WHERE group_id=%s AND tok_per_s IS NOT NULL", (gid,)).fetchall())


def test_english_switch_changes_the_prompt_language(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            gid, jid = await start(e, run_id, ["tags"], "en")
            await wait_status(e.svc, jid, "done")
    go(body)
    content = fake.requests[0]["messages"][0]["content"]
    assert "English" in content and "Greek" not in content.replace("Greek letters", "")


def test_retry_is_its_own_row_linked_to_the_first(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    bad = good("tags")
    bad["tags"][0]["name"] = "Not Kebab"
    fake = FakeLMStudio().script(bad, good("tags"))

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            gid, jid = await start(e, run_id, ["tags"])
            await wait_status(e.svc, jid, "done")
            return gid
    rows = runs(db, go(body))
    assert [(r[1], r[2], r[3]) for r in rows] == [("done", False, False), ("done", True, True)]
    first, second = db.execute("SELECT id, retry_of FROM analysis_run ORDER BY created_at, id").fetchall()
    assert second[1] == first[0]
    out = db.execute("SELECT analysis_run_id FROM output").fetchall()
    assert len(out) == 1 and out[0][0] == second[0]


def test_two_invalid_replies_leave_no_output_and_keep_the_raw_text(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio().script("{broken", "still { broken")

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            gid, jid = await start(e, run_id, ["tags"])
            await wait_status(e.svc, jid, "done")
    go(body)
    last = db.execute("SELECT schema_valid, raw_response->>'text', validation_errors, error FROM analysis_run "
                      "ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    assert last[0] is False and last[1] == "still { broken" and last[2] and "invalid JSON" in last[3]
    assert db.execute("SELECT count(*) FROM output").fetchone()[0] == 0


def test_unreachable_server_fails_the_job_naming_the_url_and_no_outputs(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))

    async def body():
        async with analysis_env(db_url, mk_settings, FakeLMStudio(), url="http://127.0.0.1:9") as e:
            gid, jid = await start(e, run_id, ["tags", "notes"])
            # skip lm_load/lm_unload: this test is about the analyse step reaching the server
            await e.svc.get(jid)
            job = await wait_status(e.svc, jid, "failed")
            return gid, job
    gid, job = go(body)
    assert "127.0.0.1:9" in job["error"]
    assert {(r[0], r[1]) for r in runs(db, gid)} == {("tags", "failed"), ("notes", "failed")}
    assert all("127.0.0.1:9" in r[4] for r in runs(db, gid) if r[0] == "tags")
    assert db.execute("SELECT count(*) FROM output").fetchone()[0] == 0


def test_cancelled_while_queued_marks_the_runs_cancelled(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))

    async def body():
        async with analysis_env(db_url, mk_settings, FakeLMStudio(), start=False) as e:
            gid, jid = await start(e, run_id, ["debrief", "tags"])
            await e.svc.cancel(jid)
            return gid
    gid = go(body)
    assert {r[1] for r in runs(db, gid)} == {"cancelled"}


def test_failure_after_the_first_kind_keeps_its_output_and_fails_the_rest(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio().script(good("debrief"), {"status": 500, "error": "model crashed"})

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            gid, jid = await start(e, run_id, ["debrief", "notes", "tags"])
            await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: {r[1] for r in runs(db, gid) if r[0] != "debrief"} == {"failed"}, what="finalizer")
            return gid
    gid = go(body)
    st = {r[0]: r[1] for r in runs(db, gid)}
    assert st == {"debrief": "done", "notes": "failed", "tags": "failed"}
    assert [r[0] for r in db.execute("SELECT kind FROM output").fetchall()] == ["debrief"]
    assert "model crashed" in [r[4] for r in runs(db, gid) if r[0] == "notes"][0]


def test_job_retry_skips_finished_kinds(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio().script(good("debrief"), {"status": 500, "error": "boom"})

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            gid, jid = await start(e, run_id, ["debrief", "tags"])
            await wait_status(e.svc, jid, "failed")
            await wait_until(lambda: {r[1] for r in runs(db, gid)} == {"done", "failed"}, what="finalizer")
            fake.script(good("tags"))
            await e.svc.retry(jid)
            await wait_status(e.svc, jid, "done")
            return gid
    gid = go(body)
    assert {r[0]: r[1] for r in runs(db, gid)} == {"debrief": "done", "tags": "done"}
    assert [r["response_format"]["json_schema"]["name"] for r in fake.requests] == ["debrief", "tags", "tags"]
