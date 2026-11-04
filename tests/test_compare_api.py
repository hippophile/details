"""compare-runs: /api/comparisons (create validations, list, header, outputs + counts, warnings, eval, orphans)."""
import asyncio, uuid

from compare_helpers import add_script, body, good_checklist, seed_analysis, seed_rec, seed_stt, side
from details.db import open_pool
from details.services import comparison
from library_helpers import run_app
from llm_helpers import good

LINES = "**Ελένη:** Καλημέρα. Τι έκανες χθες;\n\n**Νίκος:** Τελείωσα το migration στην PostgreSQL.\n"


def two_runs(db, mk_settings, **kw):
    rid = seed_rec(db, mk_settings(), **kw)
    return rid, seed_stt(db, rid, wall_ms=4200, rtf=7.1), seed_stt(db, rid, model_id="whisper-large", wall_ms=9700, rtf=3.1)


def post(c, b):
    return c.post("/api/comparisons", json=b)


def test_create_201_two_items_sides_a_b(db, db_url, mk_settings):
    rid, a, b = two_runs(db, mk_settings)

    async def fn(c, s):
        r = await post(c, body(rid, side(a), side(b)))
        assert r.status == 201
        cid = (await r.json())["id"]
        assert db.execute("SELECT side, stt_run_id::text FROM comparison_item WHERE comparison_id=%s ORDER BY side",
                          (cid,)).fetchall() == [("A", a), ("B", b)]
    run_app(mk_settings, fn)


def test_identical_sides_422_even_with_the_same_group_but_same_run_different_groups_ok(db, mk_settings):
    rid, a, b = two_runs(db, mk_settings)
    g1, g2 = seed_analysis(db, a), seed_analysis(db, a)

    async def fn(c, s):
        assert (await post(c, body(rid, side(a), side(a)))).status == 422
        assert (await post(c, body(rid, side(a, g1), side(a, g1)))).status == 422
        assert (await post(c, body(rid, side(a, g1), side(a, g2)))).status == 201
    run_app(mk_settings, fn)


def test_different_recordings_422(db, mk_settings):
    rid, a, b = two_runs(db, mk_settings)
    other = seed_rec(db, mk_settings(), title="other")
    c2 = seed_stt(db, other)

    async def fn(c, s):
        r = await post(c, body(rid, side(a), side(c2)))
        assert r.status == 422 and "recording" in (await r.json())["error"]
        assert (await post(c, body(other, side(a), side(b)))).status == 422     # recording_id does not match the runs
    run_app(mk_settings, fn)


def test_run_not_done_422_unknown_run_404_garbage_422(db, mk_settings):
    rid, a, b = two_runs(db, mk_settings)
    q = seed_stt(db, rid, status="running")

    async def fn(c, s):
        r = await post(c, body(rid, side(a), side(q)))
        assert r.status == 422 and "done" in (await r.json())["error"]
        assert (await post(c, body(rid, side(a), side(str(uuid.uuid4()))))).status == 404
        for bad in ({}, {"recording_id": rid}, body(rid, side(a), "x"), body(rid, side(a), {"stt_run_id": "nope"}),
                    body("nope", side(a), side(b)), body(rid, side(a), {"stt_run_id": b, "analysis_group_id": "zz"})):
            assert (await post(c, bad)).status == 422, bad
        r = await c.post("/api/comparisons", data="not json", headers={"Content-Type": "application/json"})
        assert r.status == 422
        assert db.execute("SELECT count(*) FROM comparison").fetchone()[0] == 0
    run_app(mk_settings, fn)


def test_foreign_group_422(db, mk_settings):
    rid, a, b = two_runs(db, mk_settings)
    g_on_a = seed_analysis(db, a)

    async def fn(c, s):
        r = await post(c, body(rid, side(a), side(b, g_on_a)))
        assert r.status == 422 and "analysis" in (await r.json())["error"]
        assert (await post(c, body(rid, side(a), side(b, str(uuid.uuid4()))))).status == 422
    run_app(mk_settings, fn)


def test_list_requires_recording_id_returns_only_that_recording_newest_first(db, mk_settings):
    rid, a, b = two_runs(db, mk_settings)
    other, c1, c2 = two_runs(db, mk_settings)

    async def fn(c, s):
        assert (await c.get("/api/comparisons")).status == 422
        assert (await c.get("/api/comparisons?recording_id=zzz")).status == 422
        i1 = (await (await post(c, body(rid, side(a), side(b)))).json())["id"]
        i2 = (await (await post(c, body(rid, side(b), side(a)))).json())["id"]
        await post(c, body(other, side(c1), side(c2)))
        lst = await (await c.get(f"/api/comparisons?recording_id={rid}")).json()
        assert [x["id"] for x in lst] == [i2, i1]
        assert lst[0]["a"]["label"] and lst[0]["b"]["label"] and lst[0]["created_at"]
        # a comparison with a missing side is never listed
        db.execute("DELETE FROM comparison_item WHERE comparison_id=%s AND side='B'", (i1,))
        assert [x["id"] for x in await (await c.get(f"/api/comparisons?recording_id={rid}")).json()] == [i2]
    run_app(mk_settings, fn)


def test_header_has_both_timings_for_a_and_ai_null_for_b(db, mk_settings):
    rid, a, b = two_runs(db, mk_settings)
    g = seed_analysis(db, a, model_id="gemma-cheap", wall_ms=12000)

    async def fn(c, s):
        cid = (await (await post(c, body(rid, side(a, g), side(b)))).json())["id"]
        d = await (await c.get(f"/api/comparisons/{cid}")).json()
        A, B = d["a"], d["b"]
        assert A["stt"]["wall_ms"] == 4200 and abs(A["stt"]["rtf"] - 7.1) < 0.01 and A["stt"]["model_label"]
        assert A["ai"]["wall_ms"] == 12000 and A["ai"]["model_label"] and A["date"]
        assert B["stt"]["wall_ms"] == 9700 and B["ai"] is None
        assert A["label"] and B["label"] and A["stt_run_id"] == a and A["analysis_group_id"] == g
        assert (await c.get(f"/api/comparisons/{uuid.uuid4()}")).status == 404
        assert (await c.get("/api/comparisons/xyz")).status == 404
    run_app(mk_settings, fn)


def test_outputs_have_counts_invalid_and_no_outputs(db, mk_settings):
    rid, a, b = two_runs(db, mk_settings)
    ga = seed_analysis(db, a, {"checklist": good_checklist(5, 2), "debrief": good("debrief")})
    gb = seed_analysis(db, b, {"checklist": good_checklist(3, 0), "debrief": good("debrief")}, valid={"debrief": False})

    async def fn(c, s):
        cid = (await (await post(c, body(rid, side(a, ga), side(b, gb)))).json())["id"]
        d = await (await c.get(f"/api/comparisons/{cid}")).json()
        ck = d["outputs"]["checklist"]
        assert ck["a"]["counts"] == {"items": 5, "done": 2, "with_due": 0, "unverified": 0}
        assert ck["b"]["counts"] == {"items": 3, "done": 0, "with_due": 0, "unverified": 0}
        assert ck["a"]["content"]["items"][0]["text"] == "i0"
        de = d["outputs"]["debrief"]
        assert de["a"]["counts"]["decisions"] == 1 and de["a"]["invalid"] is False
        assert de["b"]["invalid"] is True and de["b"]["counts"] is None
        # side B without analysis -> that kind has b: null
        cid2 = (await (await post(c, body(rid, side(a, ga), side(b)))).json())["id"]
        d2 = await (await c.get(f"/api/comparisons/{cid2}")).json()
        assert d2["outputs"]["checklist"]["b"] is None and d2["outputs"]["checklist"]["a"]["counts"]["items"] == 5
    run_app(mk_settings, fn)


def test_warning_for_different_transcripts_not_for_same_hash_or_missing_analysis(db, mk_settings):
    rid, a, b = two_runs(db, mk_settings)
    ga, gb = seed_analysis(db, a, sha="h1"), seed_analysis(db, b, sha="h2")
    ga2, gb2 = seed_analysis(db, a, sha="same"), seed_analysis(db, b, sha="same")
    ga3 = seed_analysis(db, a, sha="h1")

    async def get(c, x, y):
        cid = (await (await post(c, body(rid, x, y))).json())["id"]
        return await (await c.get(f"/api/comparisons/{cid}")).json()

    async def fn(c, s):
        d = await get(c, side(a, ga), side(b, gb))
        assert [w["code"] for w in d["warnings"]] == ["different_transcripts"]
        assert d["warnings"][0]["message"] == "Different transcripts — AI differences may come from speech-to-text."
        assert (await get(c, side(a, ga2), side(b, gb2)))["warnings"] == []       # two stt runs, same text hash
        assert (await get(c, side(a, ga), side(a, ga3)))["warnings"] == []        # same stt run
        assert (await get(c, side(a, ga), side(b)))["warnings"] == []             # B has no analysis
        assert (await get(c, side(a), side(b)))["warnings"] == []
    run_app(mk_settings, fn)


def test_transcripts_endpoint_returns_both_texts(db, mk_settings):
    rid = seed_rec(db, mk_settings())
    a, b = seed_stt(db, rid, text="το Γκέμα και"), seed_stt(db, rid, text="το Gemma και")

    async def fn(c, s):
        cid = (await (await post(c, body(rid, side(a), side(b)))).json())["id"]
        t = await (await c.get(f"/api/comparisons/{cid}/transcripts")).json()
        assert t == {"a": {"text": "το Γκέμα και"}, "b": {"text": "το Gemma και"}}
        assert (await c.get(f"/api/comparisons/{uuid.uuid4()}/transcripts")).status == 404
    run_app(mk_settings, fn)


def test_eval_computed_stored_once_and_shown(db, mk_settings):
    sid, s = add_script(db, body=LINES)
    rid = seed_rec(db, mk_settings(), sid)
    perfect = "Καλημέρα. Τι έκανες χθες; Τελείωσα το migration στην PostgreSQL."
    a, b = seed_stt(db, rid, text=perfect), seed_stt(db, rid, model_id="whisper-large", text="κάτι άλλο εντελώς")

    async def fn(c, st):
        c1 = (await (await post(c, body(rid, side(a), side(b)))).json())["id"]
        c2 = (await (await post(c, body(rid, side(a), side(b)))).json())["id"]
        assert c1 != c2
        assert db.execute("SELECT count(*) FROM eval_result").fetchone()[0] == 2
        assert db.execute("SELECT count(DISTINCT stt_run_id) FROM eval_result").fetchone()[0] == 2
        d = await (await c.get(f"/api/comparisons/{c1}")).json()
        e = d["eval"]
        assert e["a"]["wer_all"] == 0.0 and e["b"]["wer_all"] > 50
        assert {"wer_no_hesitations", "tech_terms", "numbers"} <= set(e["a"])
        assert "postgresql" in e["a"]["tech_terms"]["missed"] or e["a"]["tech_terms"]["pct"] in (None, 100)
        assert e["script"]["sha256"] == s["sha256"]
    run_app(mk_settings, fn)


def test_no_script_and_no_lines_give_eval_null_without_error(db, mk_settings):
    rid, a, b = two_runs(db, mk_settings)                                   # no script
    sid, _ = add_script(db, body="just prose, no speaker lines")
    rid2, a2, b2 = two_runs(db, mk_settings, script_id=sid)

    async def fn(c, s):
        for r, x, y in ((rid, a, b), (rid2, a2, b2)):
            resp = await post(c, body(r, side(x), side(y)))
            assert resp.status == 201
            d = await (await c.get(f"/api/comparisons/{(await resp.json())['id']}")).json()
            assert d["eval"] is None
        assert db.execute("SELECT count(*) FROM eval_result").fetchone()[0] == 0
    run_app(mk_settings, fn)


def test_deleting_one_run_removes_the_comparison_and_it_leaves_the_list(db, mk_settings):
    rid, a, b = two_runs(db, mk_settings)
    c3 = seed_stt(db, rid, model_id="whisper-large")

    async def fn(c, s):
        keep = (await (await post(c, body(rid, side(b), side(c3)))).json())["id"]
        gone = (await (await post(c, body(rid, side(a), side(b)))).json())["id"]
        assert (await c.delete(f"/api/stt-runs/{a}")).status == 200
        assert (await c.get(f"/api/comparisons/{gone}")).status == 404
        assert [x["id"] for x in await (await c.get(f"/api/comparisons?recording_id={rid}")).json()] == [keep]
        assert db.execute("SELECT count(*) FROM comparison_item").fetchone()[0] == 2
    run_app(mk_settings, fn)


def test_prune_orphans_also_drops_comparisons_whose_analysis_group_vanished(db, db_url, mk_settings):
    rid, a, b = two_runs(db, mk_settings)
    g = seed_analysis(db, a)
    cid = db.execute("INSERT INTO comparison (recording_id) VALUES (%s) RETURNING id", (rid,)).fetchone()[0]
    for sd, run, grp in (("A", a, g), ("B", b, None)):
        db.execute("INSERT INTO comparison_item (comparison_id, side, stt_run_id, analysis_group_id, label) "
                   "VALUES (%s,%s,%s,%s,'x')", (cid, sd, run, grp))

    async def go():
        pool = await open_pool(db_url)
        try:
            async with pool.connection() as conn:
                assert await comparison.prune_orphans(conn, rid) == 0       # still intact
                db.execute("DELETE FROM analysis_run WHERE group_id=%s", (g,))
                assert await comparison.prune_orphans(conn, rid) == 1
        finally:
            await pool.close()
    asyncio.run(go())
    assert db.execute("SELECT count(*) FROM comparison").fetchone()[0] == 0
