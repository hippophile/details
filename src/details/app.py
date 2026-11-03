"""Application factory. Startup order is fixed: open pool, migration check, then registered hooks in order."""
import pathlib

from aiohttp import web

from psycopg_pool import AsyncConnectionPool

from .api.live_v1 import add_live_routes
from .api.record import add_record_routes
from .db import open_pool
from .migrate import MIGRATIONS_DIR, check_pending
from .security import local_only
from .services import recordings, scripts
from .settings import REPO_ROOT, Settings

BIND_HOST = "127.0.0.1"
PAGES = ("record", "library", "analyse")
SETTINGS = web.AppKey("settings", Settings)
POOL = web.AppKey("pool", AsyncConnectionPool)


def create_app(settings, *, web_dir=None, migrations_dir=MIGRATIONS_DIR, hooks=()):
    """hooks: async callables(app) run in order after the migration check (recovery, seed sync, purge, ...)."""
    web_dir = pathlib.Path(web_dir or REPO_ROOT / "web")
    app = web.Application(middlewares=[local_only])
    app[SETTINGS] = settings

    async def health(request):
        return web.json_response({"ok": True, "db": POOL in app})

    app.router.add_get("/api/health", health)
    async def root(request):
        raise web.HTTPFound("/live")

    app.router.add_get("/", root)
    for x in PAGES:
        async def page(request, x=x):
            f = web_dir / x / "index.html"
            if not f.is_file():
                raise web.HTTPNotFound()
            return web.FileResponse(f)
        app.router.add_get(f"/{x}", page)
        if (web_dir / x).is_dir():
            app.router.add_static(f"/{x}/", web_dir / x)
    if (web_dir / "shared").is_dir():
        app.router.add_static("/shared/", web_dir / "shared")
    add_live_routes(app, settings)
    add_record_routes(app, settings, POOL)

    async def recover_hook(app):
        await recordings.recover_all(app[POOL], settings)

    async def seed_hook(app):
        await scripts.sync_scripts(app[POOL])

    hooks = (recover_hook, seed_hook, *hooks)

    async def startup(app):
        if not settings.db.url:
            return  # no database: static pages + /api/health only
        pool = await open_pool(settings.db.url, settings.db.connect_timeout)
        try:
            await check_pending(pool, migrations_dir)
            app[POOL] = pool
            for hook in hooks:
                await hook(app)
        except BaseException:
            app.pop(POOL, None)
            await pool.close()
            raise

    async def cleanup(app):
        if POOL in app:
            await app[POOL].close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app
