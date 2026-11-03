import asyncio, pathlib

from details.db import open_pool
from details.services import scripts
from details.settings import REPO_ROOT


def run_sync(db_url, seeds):
    async def go():
        pool = await open_pool(db_url)
        try:
            await scripts.sync_scripts(pool, seeds)
            async with pool.connection() as c:
                cur = await c.execute("SELECT id, slug, body_md, sha256 FROM script ORDER BY sort_order")
                return await cur.fetchall()
        finally:
            await pool.close()
    return asyncio.run(go())


def test_real_seeds_insert_four_and_are_idempotent(db, db_url):
    a = run_sync(db_url, REPO_ROOT / "db" / "seeds" / "scripts")
    assert len(a) == 4
    b = run_sync(db_url, REPO_ROOT / "db" / "seeds" / "scripts")
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert db.execute("SELECT count(*) FROM script WHERE name LIKE 'Bank BPMN review%'").fetchone()[0] == 1


def test_changed_file_updates_body_and_sha(db, db_url, tmp_path):
    d = tmp_path / "seeds"
    d.mkdir()
    f = d / "01-x.md"
    f.write_text("---\nslug: x\nname: X\n---\n**A:** one\n")
    first = run_sync(db_url, d)
    f.write_text("---\nslug: x\nname: X\n---\n**A:** two\n")
    second = run_sync(db_url, d)
    assert first[0]["id"] == second[0]["id"]
    assert "two" in second[0]["body_md"] and first[0]["sha256"] != second[0]["sha256"]
