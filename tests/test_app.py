import asyncio, logging, pathlib, shutil

import pytest
from aiohttp import web

import conftest
from conftest import client_for
from details.app import POOL, create_app
from details.settings import REPO_ROOT

DEAD_DSN = "postgresql://u:s3cret@127.0.0.1:1/x"


def run(coro):
    return asyncio.run(coro)


def test_health_and_shared_css(mk_settings):
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            r = await c.get("/api/health")
            assert r.status == 200
            body = await r.json()
            assert body["ok"] is True and body["db"] is False
            assert (await c.get("/shared/shared.css")).status == 200
    run(go())


def test_root_redirects_to_library(mk_settings):
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            r = await c.get("/", allow_redirects=False)
            assert r.status == 302
            assert r.headers["Location"] == "/library"
    run(go())


def test_v1_live_routes_are_gone(mk_settings):
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            for path in ("/live", "/models", "/ws"):
                assert (await c.get(path)).status == 404, path
    run(go())


def test_default_bind_is_localhost(mk_settings):
    from details.app import BIND_HOST
    assert BIND_HOST == "127.0.0.1"


def test_bad_dsn_error_and_logs_hide_password(mk_settings, caplog):
    caplog.set_level(logging.DEBUG)
    async def go():
        with pytest.raises(Exception) as e:
            async with client_for(create_app(mk_settings(DEAD_DSN, DETAILS_DB_CONNECT_TIMEOUT="1"))):
                pass
        return str(e.value) + repr(e.value) + repr(e.value.__cause__)
    text = run(go())
    assert "s3cret" not in text
    assert "s3cret" not in caplog.text


def test_pool_opens_and_closes(db_url, mk_settings):
    app = create_app(mk_settings(db_url))
    async def go():
        async with client_for(app) as c:
            pool = app[POOL]
            assert not pool.closed
            assert (await (await c.get("/api/health")).json())["db"] is True
        assert pool.closed
    run(go())


def test_pending_migration_blocks_startup(db_url, mk_settings, tmp_path):
    d = tmp_path / "m"
    shutil.copytree(conftest.MIGRATIONS, d)
    (d / "0099_new.sql").write_text("SELECT 1;")
    ran = []
    async def hook(app):
        ran.append("hook")
    async def go():
        with pytest.raises(Exception, match="run `make migrate`"):
            async with client_for(create_app(mk_settings(db_url), migrations_dir=d, hooks=[hook])):
                pass
    run(go())
    assert ran == []


def test_no_database_skips_hooks_but_serves(mk_settings):
    ran = []
    async def hook(app):
        ran.append("hook")
    async def go():
        async with client_for(create_app(mk_settings(), hooks=[hook])) as c:
            body = await (await c.get("/api/health")).json()
            assert body["db"] is False
    run(go())
    assert ran == []


def test_hooks_run_in_order_after_check(db_url, mk_settings):
    order = []
    def mk(n):
        async def h(app):
            assert POOL in app
            order.append(n)
        return h
    async def go():
        async with client_for(create_app(mk_settings(db_url), hooks=[mk("recovery"), mk("seed"), mk("runner")])):
            pass
    run(go())
    assert order == ["recovery", "seed", "runner"]
