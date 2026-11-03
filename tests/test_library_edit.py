import os

from library_helpers import run_app, seed

ZERO = "00000000-0000-0000-0000-000000000000"


def title_of(db, rid):
    return db.execute("SELECT title FROM recording WHERE id=%s", (rid,)).fetchone()[0]


def test_rename_trims(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, "Recording 2026-11-03 10:02")

    async def fn(c, s):
        r = await c.patch(f"/api/recordings/{rid}", json={"title": "  Standup 3 Nov "})
        return r.status, (await r.json())["title"]
    assert run_app(mk_settings, fn) == (200, "Standup 3 Nov")
    assert title_of(db, rid) == "Standup 3 Nov"


def test_rename_rejects_bad_titles(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, "keep")

    async def fn(c, s):
        out = []
        for body in ({"title": "   "}, {"title": ""}, {"title": "x" * 201}, {"title": 5}, {}):
            out.append((await c.patch(f"/api/recordings/{rid}", json=body)).status)
        out.append((await c.patch(f"/api/recordings/{rid}", data=b"not json")).status)
        out.append((await c.patch(f"/api/recordings/{rid}", json={"title": "x" * 200})).status)
        return out
    assert run_app(mk_settings, fn) == [422] * 6 + [200]


def test_rename_unchanged_after_422(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, "keep")

    async def fn(c, s):
        return (await c.patch(f"/api/recordings/{rid}", json={"title": "y" * 201})).status
    assert run_app(mk_settings, fn) == 422
    assert title_of(db, rid) == "keep"


def test_markup_title_stored_as_plain_text(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, "t")
    evil = "<img src=x onerror=alert(1)>"

    async def fn(c, s):
        await c.patch(f"/api/recordings/{rid}", json={"title": evil})
        return (await (await c.get("/api/recordings")).json())[0]["title"]
    assert run_app(mk_settings, fn) == evil


def test_delete_to_trash_then_restore(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, "t")
    f = s.paths.data_dir / "recordings" / rid / "master.flac"

    async def fn(c, s):
        d = await c.delete(f"/api/recordings/{rid}")
        mid = db.execute("SELECT deleted_at FROM recording WHERE id=%s", (rid,)).fetchone()[0]
        listed = await (await c.get("/api/recordings")).json()
        exists = f.exists()
        r = await c.post(f"/api/recordings/{rid}/restore")
        return d.status, mid, listed, exists, r.status
    d, mid, listed, exists, r = run_app(mk_settings, fn)
    assert d == 200 and mid is not None and listed == [] and exists and r == 200
    assert db.execute("SELECT deleted_at FROM recording WHERE id=%s", (rid,)).fetchone()[0] is None
    assert f.exists()


def test_unknown_id_404(db, db_url, mk_settings):
    async def fn(c, s):
        return [(await c.delete(f"/api/recordings/{ZERO}")).status,
                (await c.post(f"/api/recordings/{ZERO}/restore")).status,
                (await c.patch(f"/api/recordings/{ZERO}", json={"title": "x"})).status,
                (await c.delete("/api/recordings/nope")).status]
    assert run_app(mk_settings, fn) == [404] * 4


def test_foreign_origin_rejected_on_writes(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, "t")

    async def fn(c, s):
        h = {"Origin": "http://evil.example"}
        return [(await c.delete(f"/api/recordings/{rid}", headers=h)).status,
                (await c.patch(f"/api/recordings/{rid}", json={"title": "z"}, headers=h)).status]
    assert run_app(mk_settings, fn) == [403, 403]
    assert title_of(db, rid) == "t"
