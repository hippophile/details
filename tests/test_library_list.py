from library_helpers import ago, run_app, seed


def titles(j):
    return [r["title"] for r in j]


def test_newest_first_and_fields(db, db_url, mk_settings):
    s = mk_settings(db_url)
    seed(db, s, "early", "2026-11-03T10:00:00+00:00")
    seed(db, s, "late", "2026-11-03T11:00:00+00:00")

    async def fn(c, s):
        r = await c.get("/api/recordings")
        assert r.status == 200
        return await r.json()
    j = run_app(mk_settings, fn)
    assert titles(j) == ["late", "early"]
    assert set(j[0]) == {"id", "title", "started_at", "duration_ms", "size_bytes", "runs_count", "deleted_at"}
    assert j[0]["runs_count"] == 0 and j[0]["deleted_at"] is None and j[0]["size_bytes"] == 1000


def test_search_case_insensitive(db, db_url, mk_settings):
    s = mk_settings(db_url)
    seed(db, s, "Standup 3 Nov")
    seed(db, s, "BPMN review")

    async def fn(c, s):
        return await (await c.get("/api/recordings", params={"q": "standup"})).json()
    assert titles(run_app(mk_settings, fn)) == ["Standup 3 Nov"]


def test_search_wildcards_literal(db, db_url, mk_settings):
    s = mk_settings(db_url)
    seed(db, s, "100% done")
    seed(db, s, "plain")
    seed(db, s, "a_b")
    seed(db, s, "axb")

    async def fn(c, s):
        return [titles(await (await c.get("/api/recordings", params={"q": q})).json()) for q in ("%", "_")]
    pct, und = run_app(mk_settings, fn)
    assert pct == ["100% done"] and und == ["a_b"]


def test_trash_hidden_unless_requested(db, db_url, mk_settings):
    s = mk_settings(db_url)
    seed(db, s, "live")
    seed(db, s, "gone", deleted_at=ago(1))

    async def fn(c, s):
        a = await (await c.get("/api/recordings")).json()
        b = await (await c.get("/api/recordings", params={"trash": "1"})).json()
        return a, b
    a, b = run_app(mk_settings, fn)
    assert titles(a) == ["live"] and titles(b) == ["gone"] and b[0]["deleted_at"]


def test_get_one_and_unknown(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, "one")

    async def fn(c, s):
        ok = await c.get(f"/api/recordings/{rid}")
        return ok.status, (await ok.json())["title"], (await c.get("/api/recordings/not-a-uuid")).status, \
            (await c.get("/api/recordings/00000000-0000-0000-0000-000000000000")).status
    assert run_app(mk_settings, fn) == (200, "one", 404, 404)
