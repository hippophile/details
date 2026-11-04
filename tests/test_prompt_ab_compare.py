"""prompt-ab: pick a prompt version per analysis, version + checks in the analyses/compare payloads, v1 vs v2 compare.
FakeLMStudio HTTP mode; the model 'invents a year' unless the prompt it is given contains the v2 rule."""
import asyncio, re, shutil

from conftest import client_for
from details.app import create_app
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from jobs_helpers import wait_until
from llm_helpers import good, kind_of, seed_run
from details.settings import REPO_ROOT


def responder(body):
    k = kind_of(body)
    if k == "reduce":
        return {"text": "Σύνοψη"}
    c = good(k)
    if k == "checklist":
        v2 = "current year" in body["messages"][0]["content"]
        for it in c["items"]:
            it["due"] = None
        c["items"][0]["due"] = None if v2 else "2023-11-12"       # the bug: an invented year when the prompt is v1
    return c


def api(db_url, mk_settings, tmp_path, body, fake=None):
    fake = fake or FakeLMStudio()
    fake.responder = fake.responder or responder
    root = tmp_path / "prompts"
    shutil.copytree(REPO_ROOT / "prompts", root)

    async def go():
        url = await fake.start_http()
        s = mk_settings(db_url, DETAILS_LMSTUDIO_URL=url, DETAILS_RUNNER_CANCEL_GRACE_S="1",
                        DETAILS_PATHS_PROMPTS_DIR=str(root))
        try:
            async with client_for(create_app(s, lm_transport=fake, meminfo=FakeMemInfo())) as c:
                await body(c, s)
        finally:
            await fake.stop_http()
    asyncio.run(asyncio.wait_for(go(), 40))


async def post(c, run_id, **body):
    body = {"model_id": "gemma-cheap", "kinds": ["checklist"], "output_language": "transcript", **body}
    return await c.post(f"/api/stt-runs/{run_id}/analyses", json=body)


async def finish(c, gid):
    async def done():
        return (await (await c.get(f"/api/analyses/{gid}")).json())["status"] in ("done", "failed", "cancelled")
    await wait_until(done, what="analysis finished")
    return await (await c.get(f"/api/analyses/{gid}")).json()


def test_picked_version_is_the_template_of_the_run_and_the_default_is_the_latest(db, db_url, mk_settings, tmp_path):
    _, run_id = seed_run(db, mk_settings(db_url))

    async def body(c, s):
        r = await post(c, run_id, kinds=["checklist", "tags"], prompt_versions={"checklist": 1})
        assert r.status == 202
        g1 = await finish(c, (await r.json())["group_id"])
        g2 = await finish(c, (await (await post(c, run_id)).json())["group_id"])
        rows = {gid: db.execute("SELECT t.version, a.rendered_prompt FROM analysis_run a JOIN prompt_template t "
                                "ON t.id=a.prompt_template_id WHERE a.group_id=%s AND a.kind='checklist' "
                                "AND a.retry_of IS NULL", (gid,)).fetchone() for gid in (g1["group_id"], g2["group_id"])}
        v1, v2 = rows[g1["group_id"]], rows[g2["group_id"]]
        assert v1[0] == 1 and "current year" not in v1[1]
        assert v2[0] == 2 and "current year" in v2[1]                 # built from the v2 body
        assert g1["prompts"] == {"checklist": 1, "tags": 1} and g2["prompts"] == {"checklist": 2}
        assert [k["prompt_version"] for k in g1["kinds"]] == [1, 1] and g2["kinds"][0]["prompt_version"] == 2
        lst = await (await c.get(f"/api/stt-runs/{run_id}/analyses")).json()
        assert {x["group_id"]: x["prompts"] for x in lst} == {g1["group_id"]: {"checklist": 1, "tags": 1},
                                                              g2["group_id"]: {"checklist": 2}}
    api(db_url, mk_settings, tmp_path, body)


def test_bad_picks_are_422_and_create_nothing(db, db_url, mk_settings, tmp_path):
    _, run_id = seed_run(db, mk_settings(db_url))

    async def body(c, s):
        for pv in ({"checklist": 9}, {"tags": 1}, {"nokind": 1}, {"diagram": 1}, {"checklist": True},
                   {"checklist": "2"}, {"checklist": 1.0}, {"checklist": 0}, [1], "checklist", 2):
            r = await post(c, run_id, prompt_versions=pv)
            assert r.status == 422 and "error" in await r.json(), pv
        assert (await post(c, run_id, prompt_versions=None)).status == 202       # null = defaults
    api(db_url, mk_settings, tmp_path, body)
    assert db.execute("SELECT count(*) FROM analysis_run WHERE params->>'job_id' IS NULL").fetchone()[0] == 0


def test_bad_pick_leaves_no_rows(db, db_url, mk_settings, tmp_path):
    _, run_id = seed_run(db, mk_settings(db_url))

    async def body(c, s):
        assert (await post(c, run_id, prompt_versions={"checklist": 9})).status == 422
    api(db_url, mk_settings, tmp_path, body)
    assert db.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM job").fetchone()[0] == 0


def test_v1_and_v2_on_one_transcript_compare_with_versions_checks_and_due_counts(db, db_url, mk_settings, tmp_path):
    rid, run_id = seed_run(db, mk_settings(db_url))

    async def body(c, s):
        ga = await finish(c, (await (await post(c, run_id, prompt_versions={"checklist": 1})).json())["group_id"])
        gb = await finish(c, (await (await post(c, run_id, prompt_versions={"checklist": 2})).json())["group_id"])
        assert ga["kinds"][0]["output"]["content"]["items"][0]["due"] == "2023-11-12"       # v1 kept the invented year
        assert gb["kinds"][0]["schema_valid"] is True and gb["kinds"][0]["output"]["content"]["items"][0]["due"] is None
        for g in (ga, gb):
            assert re.fullmatch(r"Analysis #\d+ · .+ · .+", g["label"])
        r = await c.post("/api/comparisons", json={"recording_id": rid, "a": {"stt_run_id": run_id,
                         "analysis_group_id": ga["group_id"]}, "b": {"stt_run_id": run_id,
                         "analysis_group_id": gb["group_id"]}})
        assert r.status == 201
        j = await (await c.get(f"/api/comparisons/{(await r.json())['id']}")).json()
        assert j["warnings"] == []
        ck = j["outputs"]["checklist"]
        assert ck["a"]["prompt"] == {"name": "checklist", "version": 1, "checks": []}
        assert ck["b"]["prompt"] == {"name": "checklist", "version": 2, "checks": ["due_year_in_transcript"]}
        assert ck["a"]["counts"]["with_due"] == 1 and ck["b"]["counts"]["with_due"] == 0
        assert ck["a"]["counts"]["items"] == ck["b"]["counts"]["items"] == 3
        assert j["a"]["ai"]["label"] == ga["label"] and j["b"]["label"].endswith(gb["label"])     # label format unchanged
    api(db_url, mk_settings, tmp_path, body)


def test_invented_year_on_v2_fails_after_the_retry_while_v1_accepts_it(db, db_url, mk_settings, tmp_path):
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.responder = lambda b: good("checklist") if kind_of(b) == "checklist" else good(kind_of(b))   # always due 2026-11-12

    async def body(c, s):
        g1 = await finish(c, (await (await post(c, run_id, prompt_versions={"checklist": 1})).json())["group_id"])
        g2 = await finish(c, (await (await post(c, run_id, prompt_versions={"checklist": 2})).json())["group_id"])
        assert g1["kinds"][0]["schema_valid"] is True
        k = g2["kinds"][0]
        assert k["schema_valid"] is False and k["attempts"] == 2 and k["output"] is None and "2026" in " ".join(k["validation_errors"])
    api(db_url, mk_settings, tmp_path, body, fake)
