"""Prompts API: GET /api/prompts, GET /api/prompts/{kind}/{version}, POST /api/prompts/{kind} (save as a new version).
Old versions are never edited: a save writes prompts/<kind>/v<N+1>.md and inserts a new prompt_template row."""
from aiohttp import web

from ..llm import prompts
from ..llm.schemas import KINDS


def add_prompts_routes(app, settings, pool_key):
    def pool(request):
        return request.app[pool_key]

    def err(msg, status):
        return web.json_response({"error": msg}, status=status)

    def root():
        return settings.paths.prompts_dir

    async def list_(request):
        async with pool(request).connection() as c:
            cur = await c.execute("SELECT p.kind, p.version, p.sha256, p.created_at, p.checks, "
                                  "(SELECT count(*) FROM analysis_run a WHERE a.prompt_template_id=p.id "
                                  "AND a.retry_of IS NULL) AS runs FROM prompt_template p ORDER BY p.kind, p.version")
            rows = await cur.fetchall()
        kinds = []
        for kind in KINDS:
            vs = [{"version": r["version"], "sha256": r["sha256"], "created_at": r["created_at"].isoformat(),
                   "checks": list(r["checks"]), "runs": r["runs"]} for r in rows if r["kind"] == kind]
            if vs:
                kinds.append({"kind": kind, "latest": vs[-1]["version"], "versions": vs})
        return web.json_response({"kinds": kinds})

    async def detail(request):
        kind = request.match_info["kind"]
        v = request.match_info["version"]
        version = int(v) if v.isdigit() else 0
        if kind not in KINDS or version < 1:
            return err("prompt version not found", 404)
        async with pool(request).connection() as c:
            r = await (await c.execute("SELECT version, sha256, body, checks, created_at, "
                                       "(SELECT max(version) FROM prompt_template WHERE kind=%s) AS latest "
                                       "FROM prompt_template WHERE kind=%s AND version=%s ORDER BY name LIMIT 1",
                                       (kind, kind, version))).fetchone()
        if r is None:
            return err("prompt version not found", 404)
        return web.json_response({"kind": kind, "version": r["version"], "latest": r["latest"], "body": r["body"],
                                  "checks": list(r["checks"]), "sha256": r["sha256"],
                                  "created_at": r["created_at"].isoformat(),
                                  "note": prompts.read_note(root(), kind, version)})

    async def save(request):
        kind = request.match_info["kind"]
        if kind not in KINDS:                       # before any path is built
            return err("unknown prompt kind", 404)
        try:
            data = await request.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return err("expected a JSON object", 422)
        try:
            out = await prompts.save_new_version(pool(request), root(), kind, data.get("base_version"),
                                                 data.get("body"), data.get("note"))
        except prompts.PromptSaveError as e:
            return err(e.message, e.status)
        return web.json_response(out, status=201)

    app.router.add_get("/api/prompts", list_)
    app.router.add_get("/api/prompts/{kind}/{version}", detail)
    app.router.add_post("/api/prompts/{kind}", save)
