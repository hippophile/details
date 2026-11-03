"""Seed test scripts: db/seeds/scripts/NN-slug.md (front matter slug, name) synced into `script` by slug."""
import hashlib, pathlib, re

from ..settings import REPO_ROOT

SEEDS_DIR = REPO_ROOT / "db" / "seeds" / "scripts"
FRONT = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)


def parse(path):
    p = pathlib.Path(path)
    m = FRONT.match(p.read_text())
    if not m:
        raise ValueError(f"{p.name}: missing front matter")
    meta = dict(l.split(":", 1) for l in m.group(1).splitlines() if ":" in l)
    slug, name = meta.get("slug", "").strip(), meta.get("name", "").strip()
    if not slug or not name:
        raise ValueError(f"{p.name}: front matter needs slug and name")
    order = re.match(r"(\d+)", p.name)
    body = m.group(2)
    return {"slug": slug, "name": name, "body_md": body, "sha256": hashlib.sha256(body.encode()).hexdigest(),
            "sort_order": int(order.group(1)) if order else 0}


async def sync_scripts(pool, seeds_dir=SEEDS_DIR):
    items = [parse(p) for p in sorted(pathlib.Path(seeds_dir).glob("*.md"))]
    async with pool.connection() as c:
        for s in items:
            await c.execute(
                "INSERT INTO script (slug, name, body_md, sha256, sort_order) "
                "VALUES (%(slug)s, %(name)s, %(body_md)s, %(sha256)s, %(sort_order)s) "
                "ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name, body_md=EXCLUDED.body_md, "
                "sha256=EXCLUDED.sha256, sort_order=EXCLUDED.sort_order", s)


async def list_scripts(pool):
    async with pool.connection() as c:
        cur = await c.execute("SELECT id, slug, name, body_md FROM script ORDER BY sort_order, id")
        return await cur.fetchall()
