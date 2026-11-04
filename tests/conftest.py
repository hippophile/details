"""Shared fixtures: settings factory, async test client, and the isolated test database."""
import atexit, contextlib, functools, pathlib, re, shutil, tempfile

import pytest
from aiohttp.test_utils import TestClient, TestServer

from details.settings import REPO_ROOT, load_settings, read_dotenv

MIGRATIONS = REPO_ROOT / "db" / "migrations"


@functools.cache
def v1_prompts_dir():
    """A tmp copy of the shipped v1 prompt files: the default `[paths] prompts_dir` of every test, so no test reads
    (v2 of checklist) or writes the repo's prompts/. Tests that need other versions pass their own dir."""
    tmp = tempfile.mkdtemp(prefix="details-prompts-v1-")
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    for f in sorted((REPO_ROOT / "prompts").glob("*/v1.md")):
        (pathlib.Path(tmp) / f.parent.name).mkdir(exist_ok=True)
        shutil.copy(f, pathlib.Path(tmp) / f.parent.name / f.name)
    return pathlib.Path(tmp)


def make_settings(data_dir, db_url=None, **env):
    """Settings from the repo config.toml with an explicit env; never reads the real env or .env."""
    e = {"DETAILS_PATHS_DATA_DIR": str(data_dir), "DETAILS_PATHS_PROMPTS_DIR": str(v1_prompts_dir()), **env}
    if db_url:
        e["DATABASE_URL"] = db_url
    return load_settings(env=e, dotenv_path=None)


@pytest.fixture
def mk_settings(tmp_path):
    return lambda db_url=None, **env: make_settings(tmp_path / "data", db_url, **env)


@contextlib.asynccontextmanager
async def client_for(app):
    async with TestClient(TestServer(app, host="127.0.0.1")) as c:
        yield c


# ---- test database -------------------------------------------------------------------------------------

def reset_test_db(url):
    """DROP/CREATE the public schema of the test DB. Refuses any database not named *_test."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    name = conninfo_to_dict(url).get("dbname", "")
    if not name.endswith("_test"):
        raise RuntimeError(f"refusing to reset database {name!r}: name must end in _test")
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")


def truncate_all(conn):
    rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "AND tablename <> 'schema_migration'").fetchall()
    if rows:
        names = ", ".join(f'"{r[0]}"' for r in rows)
        conn.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")


class _SecretDSN(str):
    """A DSN that works as a str but never shows its password in repr (pytest prints fixture values)."""
    def __repr__(self):
        import re
        return repr(re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", str(self)))


def _test_url():
    import os
    url = os.environ.get("DATABASE_URL_TEST") or read_dotenv(REPO_ROOT / ".env").get("DATABASE_URL_TEST")
    return _SecretDSN(url) if url else url


@pytest.fixture(scope="session")
def db_url():
    url = _test_url()
    if not url:
        pytest.skip("DATABASE_URL_TEST is not set (env or .env): DB tests skipped")
    from details.db import connect
    from details.migrate import migrate
    reset_test_db(url)
    with connect(url) as c:
        migrate(c, MIGRATIONS)
    return url


@pytest.fixture
def db(db_url):
    from details.db import connect
    with connect(db_url) as c:
        truncate_all(c)
        yield c


@pytest.fixture
def blank_db(db_url):
    """Empty schema for tests that exercise the migration runner itself; real migrations restored after."""
    from details.db import connect
    from details.migrate import migrate
    reset_test_db(db_url)
    yield db_url
    reset_test_db(db_url)
    with connect(db_url) as c:
        migrate(c, MIGRATIONS)
