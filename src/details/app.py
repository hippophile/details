"""Application factory. Startup order is fixed: open pool, migration check, then registered hooks in order."""
import asyncio, importlib, importlib.util, pathlib

from aiohttp import web

from psycopg_pool import AsyncConnectionPool

from .api.analyses import add_analyses_routes
from .api.comparisons import add_comparisons_routes
from .api.insights import add_insights_routes
from .api.jobs import JOBS, add_jobs_routes
from .api.live_v1 import add_live_routes
from .api.models import add_models_routes
from .api.record import add_record_routes
from .api.recordings import add_recordings_routes
from .api.stt_runs import add_stt_runs_routes
from .db import open_pool
from .llm.lmstudio_ctl import CliTransport, Controller
from .migrate import MIGRATIONS_DIR, check_pending
from .runner.joblog import JobLogs
from .runner.lease import GpuLease
from .runner.memory import MemoryManager, VmStatMemInfo
from .runner.runner import JobRunner
from .runner.steps import steps as default_steps
from .security import local_only
from .services import recordings, scripts, stt_runs, trash
from .services.jobs import JobService
from .services.models import load_registry, resolve_path
from .settings import REPO_ROOT, Settings

BIND_HOST = "127.0.0.1"
PAGES = ("record", "library", "analyse")
SETTINGS = web.AppKey("settings", Settings)
POOL = web.AppKey("pool", AsyncConnectionPool)
PURGE_TASK = web.AppKey("purge_task", asyncio.Task)
RUNNER = web.AppKey("runner", JobRunner)
LEASE = web.AppKey("lease", GpuLease)          # the ONE GpuLease: the runner and the live assistant share it
JOBLOGS = web.AppKey("joblogs", JobLogs)
STEP_MODULES = ("details.runner.steps_stt", "details.runner.steps_analyse")   # owned by stt-runs / llm-outputs


def create_app(settings, *, web_dir=None, migrations_dir=MIGRATIONS_DIR, hooks=(), step_registry=None,
               lm_transport=None, meminfo=None):
    """hooks: async callables(app) run in order after the migration check (recovery, seed sync, purge, ...).
    step_registry / lm_transport / meminfo: injection points for tests (defaults: the real ones)."""
    web_dir = pathlib.Path(web_dir or REPO_ROOT / "web")
    app = web.Application(middlewares=[local_only])
    app[SETTINGS] = settings
    app[LEASE] = lease = GpuLease()
    app[JOBLOGS] = joblogs = JobLogs.for_settings(settings)

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
    add_recordings_routes(app, settings, POOL)
    registry = load_registry(resolve_path(settings))
    ctl = Controller(lm_transport or CliTransport(settings))
    memory = MemoryManager(ctl, registry, settings, meminfo or VmStatMemInfo())
    if step_registry is None:
        step_registry = default_steps
        for mod in STEP_MODULES:      # absent until those changes land
            if importlib.util.find_spec(mod) is not None:
                importlib.import_module(mod)
    add_models_routes(app, settings, registry, ctl)
    add_jobs_routes(app)
    add_insights_routes(app, POOL)
    add_stt_runs_routes(app, settings, POOL, registry)
    add_analyses_routes(app, settings, POOL, registry)
    add_comparisons_routes(app, settings, POOL, registry)

    async def recover_hook(app):
        await recordings.recover_all(app[POOL], settings)

    async def seed_hook(app):
        await scripts.sync_scripts(app[POOL])

    async def purge_hook(app):
        await trash.purge(app[POOL], settings)
        app[PURGE_TASK] = asyncio.create_task(trash.purge_loop(app[POOL], settings))

    async def stt_hook(app):
        from .runner import steps_stt
        steps_stt.bind(app[POOL])
        await stt_runs.adopt_v1_transcripts(app[POOL], settings)

    async def prompts_hook(app):
        from .llm.prompts import sync_prompts
        from .runner import steps_analyse
        steps_analyse.bind(app[POOL], memory=memory, settings=settings, lease=lease)
        await sync_prompts(app[POOL])

    async def runner_hook(app):
        svc = JobService(app[POOL], step_registry, registry, joblogs=joblogs)
        app[JOBS] = svc
        app[RUNNER] = JobRunner(svc, settings, memory, step_registry, lease=lease, joblogs=joblogs)
        await app[RUNNER].start()

    hooks = (recover_hook, seed_hook, purge_hook, stt_hook, prompts_hook, runner_hook, *hooks)

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
            if RUNNER in app:
                await app[RUNNER].stop()
            app.pop(POOL, None)
            await pool.close()
            raise

    async def cleanup(app):
        if RUNNER in app:
            await app[RUNNER].stop()
        if PURGE_TASK in app:
            app[PURGE_TASK].cancel()
        if POOL in app:
            await app[POOL].close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app
