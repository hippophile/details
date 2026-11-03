import os

from library_helpers import ago, run_app, seed

BODY = bytes(range(256)) * 4000  # ~1 MB, every offset distinguishable


def test_full_body(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, body=BODY)

    async def fn(c, s):
        r = await c.get(f"/api/recordings/{rid}/audio")
        return r.status, r.headers, await r.read()
    st, h, b = run_app(mk_settings, fn)
    assert st == 200 and h["Accept-Ranges"] == "bytes" and b == BODY
    assert h["Content-Type"].startswith("audio/") and "flac" in h["Content-Type"]


def test_range_206(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, body=BODY)

    async def fn(c, s):
        r = await c.get(f"/api/recordings/{rid}/audio", headers={"Range": "bytes=100-199"})
        o = await c.get(f"/api/recordings/{rid}/audio", headers={"Range": "bytes=999000-"})
        z = await c.get(f"/api/recordings/{rid}/audio", headers={"Range": "bytes=0-99"})
        return r, await r.read(), o, await o.read(), z.status, z.headers["Content-Range"]
    r, rb, o, ob, zs, zc = run_app(mk_settings, fn)
    assert r.status == 206 and r.headers["Content-Range"] == f"bytes 100-199/{len(BODY)}" and rb == BODY[100:200]
    assert o.status == 206 and ob == BODY[999000:] and zs == 206 and zc == f"bytes 0-99/{len(BODY)}"


def test_range_unsatisfiable_416(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, body=BODY)

    async def fn(c, s):
        return (await c.get(f"/api/recordings/{rid}/audio", headers={"Range": "bytes=99999999-"})).status
    assert run_app(mk_settings, fn) == 416


def test_trashed_and_unknown_404(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, deleted_at=ago(1))

    async def fn(c, s):
        return [(await c.get(f"/api/recordings/{rid}/audio")).status,
                (await c.get("/api/recordings/00000000-0000-0000-0000-000000000000/audio")).status,
                (await c.get("/api/recordings/zzz/audio")).status]
    assert run_app(mk_settings, fn) == [404] * 3


def test_traversal_refused(db, db_url, mk_settings, tmp_path):
    s = mk_settings(db_url)
    secret = s.paths.data_dir / "secret.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(b"TOPSECRET")
    outside = os.path.relpath(secret, s.paths.data_dir)  # "secret.txt": inside data dir but not inside recordings/
    r1 = seed(db, s, audio_path="../../etc/hosts")
    r2 = seed(db, s, audio_path=outside)
    r3 = seed(db, s, audio_path="/etc/hosts")

    async def fn(c, s):
        out = []
        for rid in (r1, r2, r3):
            r = await c.get(f"/api/recordings/{rid}/audio")
            out.append((r.status, await r.read()))
        return out
    for st, body in run_app(mk_settings, fn):
        assert st in (403, 404) and b"TOPSECRET" not in body and b"localhost" not in body
