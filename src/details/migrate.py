"""Plain-SQL migrations: db/migrations/NNNN_name.sql, applied in order, one transaction each.
CLI: python -m details.migrate"""
import hashlib, pathlib, re

from .db import connect
from .settings import REPO_ROOT, load_settings

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
NAME = re.compile(r"^(\d{4})_[\w-]+\.sql$")


class MigrationError(RuntimeError):
    pass


class MigrationPending(RuntimeError):
    pass


def files(directory):
    out = []
    for p in sorted(pathlib.Path(directory).glob("*.sql")):
        m = NAME.match(p.name)
        if not m:
            raise MigrationError(f"bad migration file name: {p.name} (want NNNN_name.sql)")
        out.append((int(m.group(1)), p.stem, p.read_bytes()))
    return out


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def migrate(conn, directory=MIGRATIONS_DIR):
    """Apply pending files; return the applied names. conn must be autocommit (db.connect)."""
    todo = files(directory)
    exists = conn.execute("SELECT to_regclass('schema_migration')").fetchone()[0] is not None
    applied = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT version, name, sha256 FROM schema_migration")} \
        if exists else {}
    for version, name, data in todo:  # check everything first: an edited file applies nothing further
        if version in applied and applied[version][1] != _sha(data):
            raise MigrationError(f"migration {name} was edited after it was applied")
    done = []
    for version, name, data in todo:
        if version in applied:
            continue
        with conn.transaction():
            conn.execute(data.decode())
            conn.execute("INSERT INTO schema_migration (version, name, sha256) VALUES (%s, %s, %s)",
                         (version, name, _sha(data)))
        done.append(name)
    return done


async def check_pending(pool, directory=MIGRATIONS_DIR):
    """Raise MigrationPending if any migration file has not been applied (async, for app startup)."""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT to_regclass('schema_migration') AS t")
        if (await cur.fetchone())["t"] is None:
            applied = set()
        else:
            cur = await conn.execute("SELECT version FROM schema_migration")
            applied = {r["version"] for r in await cur.fetchall()}
    if any(v not in applied for v, _, _ in files(directory)):
        raise MigrationPending("database not migrated: run `make migrate`")


def main():
    s = load_settings()
    if not s.db.url:
        raise SystemExit("DATABASE_URL is not set (env or .env)")
    with connect(s.db.url, s.db.connect_timeout) as c:
        done = migrate(c)
    print("applied: " + ", ".join(done) if done else "nothing to apply")


if __name__ == "__main__":
    main()
