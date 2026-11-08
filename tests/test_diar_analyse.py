"""speakers-diarization: speaker-labelled analyses (FakeLMStudio HTTP mode as in test_llm_steps) and the pipeline preface."""
import asyncio, hashlib

import pytest

from conftest import client_for
from details.api import analyses
from details.app import create_app
from details.llm import pipeline
from details.services import diarization
from diar_helpers import SEGMENTS, insert_diar_run, seed_transcript
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from jobs_helpers import wait_status, wait_until
from llm_helpers import SEGS, TEMPLATE, ScriptedChat, analysis_env, good, good_responder, sync_prompts
from stt_helpers import insert_run, rec

LEGEND = diarization.LEGEND
LEGEND_TEXT = ("Each line starts with the speaker's name and a colon. Speaker labels are automatic and can be wrong. "
               "Never put a speaker name inside a quote.")
# merge_gap_ms 1500: the 7 seeded segments (gaps of 1000 ms) become one line per speaker
LINES = ["Speaker 1: Καλημέρα σε όλους. Θα κάνουμε deploy στις 12 Νοεμβρίου.",
         "Νίκος: Ο Νίκος θα ετοιμάσει το documentation. Υπάρχει ρίσκο με το GDPR.",
         "Speaker 3: Δεν ξέρουμε ακόμα ποιο μοντέλο. Ας το δούμε την Παρασκευή. Τέλος της συνάντησης."]


def go(fn, timeout=40):
    return asyncio.run(asyncio.wait_for(fn(), timeout))


def runs(db, gid):
    return db.execute("SELECT kind, status, retry_of IS NOT NULL, rendered_prompt, input_text_sha256, error, "
                      "diarization_run_id::text FROM analysis_run WHERE group_id=%s ORDER BY created_at, id",
                      (gid,)).fetchall()


async def start(e, run_id, kinds=("checklist",), dr=None):
    return await analyses.start_analysis(e.pool, e.svc, e.models, e.settings, run_id, "gemma-cheap", list(kinds),
                                         "transcript", diarization_run_id=dr)


def setup(db, db_url, mk_settings, names=None):
    sync_prompts(db_url)
    rid, run_id = seed_transcript(db, mk_settings(db_url))
    return rid, run_id, insert_diar_run(db, rid, names=names if names is not None else {2: "Νίκος"})


def test_legend_sentence_is_the_fixed_text():
    assert LEGEND == LEGEND_TEXT


def test_labelled_prompt_has_names_legend_and_a_different_sha(db, db_url, mk_settings):
    rid, run_id, dr = setup(db, db_url, mk_settings)
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            g1, j1 = await start(e, run_id, dr=dr)
            await wait_status(e.svc, j1, "done")
            g2, j2 = await start(e, run_id)
            await wait_status(e.svc, j2, "done")
            return g1, g2
    g1, g2 = go(body)
    labelled, plain = runs(db, g1)[0], runs(db, g2)[0]
    prompt = labelled[3]
    assert "\nΝίκος: " in prompt and "\nSpeaker 1: " in prompt and LEGEND_TEXT in prompt
    for line in LINES:
        assert line in prompt
    assert prompt.split('Transcript:\n"""\n')[1].startswith(LEGEND_TEXT)
    assert labelled[4] == hashlib.sha256("\n".join(LINES).encode()).hexdigest() and labelled[4] != plain[4]
    assert labelled[6] == dr and plain[6] is None


def test_plain_analysis_prompt_is_unchanged(db, db_url, mk_settings):
    rid, run_id, dr = setup(db, db_url, mk_settings)
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            g, j = await start(e, run_id)
            await wait_status(e.svc, j, "done")
            return g
    prompt = runs(db, go(body))[0][3]
    assert LEGEND_TEXT not in prompt and "Speaker 1" not in prompt
    assert 'Transcript:\n"""\n' + "\n".join(t for _, _, t in SEGMENTS) + '\n"""' in prompt


def test_quote_inside_one_line_verifies(db, db_url, mk_settings):
    rid, run_id, dr = setup(db, db_url, mk_settings)
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            g, j = await start(e, run_id, dr=dr)
            await wait_status(e.svc, j, "done")
    go(body)
    got = db.execute("SELECT content->'items'->0->>'verified', content->'items'->0->>'quote' FROM output "
                     "WHERE kind='checklist'").fetchone()
    assert got == ("true", "Θα κάνουμε deploy στις 12 Νοεμβρίου")


def test_large_transcript_keeps_the_legend_in_every_chunk_and_the_plain_one_has_none(db, db_url, mk_settings):
    rid, run_id, dr = setup(db, db_url, mk_settings)
    fake = FakeLMStudio()
    fake.responder = good_responder
    knobs = dict(DETAILS_LLM_SINGLE_PASS_MAX_TOKENS="30", DETAILS_LLM_CHUNK_TOKENS="40", DETAILS_LLM_CHUNK_OVERLAP_TOKENS="0")

    async def body():
        async with analysis_env(db_url, mk_settings, fake, **knobs) as e:
            g1, j1 = await start(e, run_id, ("tags",), dr=dr)
            await wait_status(e.svc, j1, "done")
            n_labelled = len(fake.requests)
            g2, j2 = await start(e, run_id, ("tags",))
            await wait_status(e.svc, j2, "done")
            return n_labelled
    n_labelled = go(body)
    labelled, plain = fake.requests[:n_labelled], fake.requests[n_labelled:]
    assert n_labelled >= 2 and len(plain) >= 2                              # both really were split into chunks
    for r in labelled:
        text = r["messages"][0]["content"]
        assert text.split('Transcript:\n"""\n')[1].startswith(LEGEND_TEXT)
    assert not [r for r in plain if LEGEND_TEXT in r["messages"][0]["content"]]


def test_pipeline_preface_goes_before_every_prompt_and_into_the_cache_key(mk_settings, tmp_path):
    s = mk_settings(DETAILS_LLM_SINGLE_PASS_MAX_TOKENS="10", DETAILS_LLM_CHUNK_TOKENS="20", DETAILS_LLM_CHUNK_OVERLAP_TOKENS="0")

    def gen(chat, **kw):
        async def run():
            return await pipeline.generate_kind(chat, settings=s, kind="tags", template=TEMPLATE, model="m", context=100_000,
                                                segments=SEGS, language="Greek", title="T", data_dir=tmp_path, **kw)
        return asyncio.run(asyncio.wait_for(run(), 20))
    plain_chat, pref_chat, pref_again = ScriptedChat(default=good("tags")), ScriptedChat(default=good("tags")), ScriptedChat(default=good("tags"))
    r1 = gen(plain_chat)
    r2 = gen(pref_chat, preface="PREFACE.")
    r3 = gen(pref_again, preface="PREFACE.")
    assert r1.mode == r2.mode == "map-reduce" and r1.chunks >= 2
    assert not [c for c in plain_chat.calls if "PREFACE." in c["messages"][0]["content"]]
    assert len(pref_chat.calls) == r2.chunks and r2.cache_hits == 0          # the plain run's cache was not reused
    for c in pref_chat.calls:
        assert c["messages"][0]["content"].startswith("Lang: Greek Title: T\nPREFACE.\n\n")
    assert r3.cache_hits == r3.chunks and pref_again.calls == []             # the same prefaced run does hit its own cache


def test_default_preface_changes_nothing(mk_settings, tmp_path):
    chat = ScriptedChat(good("tags"))
    s = mk_settings()

    async def run():
        return await pipeline.generate_kind(chat, settings=s, kind="tags", template=TEMPLATE, model="m", context=100_000,
                                            segments=SEGS, language="Greek", title="T", data_dir=tmp_path)
    asyncio.run(asyncio.wait_for(run(), 20))
    assert chat.calls[0]["messages"][0]["content"] == "Lang: Greek Title: T\n" + "\n".join(SEGS)


def test_retry_row_keeps_the_diarization_run_id(db, db_url, mk_settings):
    rid, run_id, dr = setup(db, db_url, mk_settings)
    bad = good("tags")
    bad["tags"][0]["name"] = "Not Kebab"
    fake = FakeLMStudio().script(bad, good("tags"))

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            g, j = await start(e, run_id, ("tags",), dr=dr)
            await wait_status(e.svc, j, "done")
            return g
    rows = runs(db, go(body))
    assert [(r[2], r[6]) for r in rows] == [(False, dr), (True, dr)]
    assert rows[1][3].count(LEGEND_TEXT) == 1                           # the retry prompt shows the legend once, not twice


def test_refused_combinations_422_and_no_job(db, db_url, mk_settings):
    sync_prompts(db_url)
    s = mk_settings(db_url)
    rid, run_id = seed_transcript(db, s)
    other_rid, _ = seed_transcript(db, s)
    foreign = insert_diar_run(db, other_rid)
    running = insert_diar_run(db, rid, status="running")
    v1 = insert_run(db, rid, status="done", model_id="unknown (v1 import)", text="plain text only")
    queued_before = db.execute("SELECT count(*) FROM job").fetchone()[0]

    async def body():
        async with analysis_env(db_url, mk_settings, FakeLMStudio(), start=False) as e:
            for bad, target in ((foreign, run_id), (running, run_id), ("nope", run_id), (5, run_id),
                                ("00000000-0000-0000-0000-000000000000", run_id), (insert_diar_run(db, rid), v1)):
                with pytest.raises(analyses.StartError) as x:
                    await start(e, target, dr=bad)
                assert x.value.status == 422, bad
    go(body)
    assert db.execute("SELECT count(*) FROM job").fetchone()[0] == queued_before
    assert db.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0


def test_run_deleted_before_the_step_fails_and_stores_no_output(db, db_url, mk_settings):
    rid, run_id, dr = setup(db, db_url, mk_settings)
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def body():
        async with analysis_env(db_url, mk_settings, fake, start=False) as e:
            g, j = await start(e, run_id, dr=dr)
            db.execute("DELETE FROM diarization_run WHERE id=%s", (dr,))
            await e.runner.start()
            job = await wait_status(e.svc, j, "failed")
            await wait_until(lambda: runs(db, g)[0][1] == "failed", what="rows failed")
            return g, job
    g, job = go(body)
    assert "speaker labels" in job["error"] and "speaker labels" in runs(db, g)[0][5]
    assert db.execute("SELECT count(*) FROM output").fetchone()[0] == 0
    assert fake.requests == []                                          # it never ran unlabelled


def test_group_payload_and_list_carry_the_diarization_run_id(db, db_url, mk_settings):
    sync_prompts(db_url)
    rid, run_id, dr = setup(db, db_url, mk_settings)
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def run_api():
        url = await fake.start_http()
        s = mk_settings(db_url, DETAILS_LMSTUDIO_URL=url)
        try:
            async with client_for(create_app(s, lm_transport=fake, meminfo=FakeMemInfo())) as c:
                r = await c.post(f"/api/stt-runs/{run_id}/analyses", json={
                    "model_id": "gemma-cheap", "kinds": ["tags"], "diarization_run_id": dr})
                assert r.status == 202
                gid = (await r.json())["group_id"]

                async def done():
                    return (await (await c.get(f"/api/analyses/{gid}")).json())["status"] == "done"
                await wait_until(done, what="done")
                g = await (await c.get(f"/api/analyses/{gid}")).json()
                lst = await (await c.get(f"/api/stt-runs/{run_id}/analyses")).json()
                r2 = await c.post(f"/api/stt-runs/{run_id}/analyses", json={
                    "model_id": "gemma-cheap", "kinds": ["tags"], "diarization_run_id": "nope"})
                plain = await c.post(f"/api/stt-runs/{run_id}/analyses", json={"model_id": "gemma-cheap", "kinds": ["tags"]})
                gp = await (await c.get(f"/api/analyses/{(await plain.json())['group_id']}")).json()
                return g, lst, r2.status, gp
        finally:
            await fake.stop_http()
    g, lst, bad_status, gp = go(run_api)
    assert g["diarization_run_id"] == dr and lst[-1]["diarization_run_id"] == dr
    assert bad_status == 422 and gp["diarization_run_id"] is None
