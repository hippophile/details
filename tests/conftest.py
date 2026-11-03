"""Shared fixtures: settings factory, async test client, and the isolated test database."""
import contextlib, pathlib, re

import pytest
from aiohttp.test_utils import TestClient, TestServer

from details.settings import REPO_ROOT, load_settings, read_dotenv

MIGRATIONS = REPO_ROOT / "db" / "migrations"


def make_settings(data_dir, db_url=None, **env):
    """Settings from the repo config.toml with an explicit env; never reads the real env or .env."""
    e = {"DETAILS_PATHS_DATA_DIR": str(data_dir), **env}
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


def _test_url():
    import os
    return os.environ.get("DATABASE_URL_TEST") or read_dotenv(REPO_ROOT / ".env").get("DATABASE_URL_TEST")


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
