"""Run comparison: create/list/get, per-kind output counts, different-transcripts warning, script scores.
A comparison is a read-only reference to two sides (an STT run + optionally one analysis group); nothing is copied."""
import asyncio, datetime, uuid

from psycopg.types.json import Jsonb

from ..llm.schemas import KINDS
from . import library, scoring

WARN_DIFFERENT = {"code": "different_transcripts",
                  "message": "Different transcripts — AI differences may come from speech-to-text."}


class CompareError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _items(content, *path):
    """content[path0][path1]... as a list of dicts; anything odd counts as empty."""
    cur = content
    for p in path:
        cur = cur.get(p) if isinstance(cur, dict) else None
    return [i for i in cur if isinstance(i, dict)] if isinstance(cur, list) else []


def output_counts(kind, content):
    """Counts shown next to each other in the compare view; every kind also has `unverified` (verified:false items)."""
    if kind == "debrief":
        lists = {k: _items(content, k) for k in ("decisions", "action_items", "risks", "open_questions")}
        counts = {k: len(v) for k, v in lists.items()}
        all_items = [i for v in lists.values() for i in v]
    elif kind == "notes":
        sections = _items(content, "sections")
        all_items = [b for s in sections for b in _items(s, "bullets")]
        counts = {"sections": len(sections), "bullets": len(all_items)}
    elif kind == "checklist":
        all_items = _items(content, "items")
        counts = {"items": len(all_items), "done": sum(1 for i in all_items if i.get("done") is True),
                  "with_due": sum(1 for i in all_items if i.get("due"))}
    elif kind == "tags":
        all_items = _items(content, "tags")
        counts = {"tags": len(all_items)}
    else:
        all_items, counts = [], {}
    counts["unverified"] = sum(1 for i in all_items if i.get("verified") is False)
    return counts


def _plain(v):
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime.datetime):
        return v.isoformat()
    return v


async def prune_orphans(conn, recording_id=None):
    """Delete comparisons left with fewer than 2 items (a run was deleted, or an item's analysis group no longer
    exists). Takes the caller's connection so it runs in the delete's transaction. -> number of comparisons removed."""
    rec_sql, args = ("AND c.recording_id=%s", (recording_id,)) if recording_id else ("", ())
    await conn.execute("DELETE FROM comparison_item i USING comparison c WHERE c.id=i.comparison_id "
                       "AND i.analysis_group_id IS NOT NULL AND NOT EXISTS "
                       f"(SELECT 1 FROM analysis_run a WHERE a.group_id=i.analysis_group_id) {rec_sql}", args)
    cur = await conn.execute("DELETE FROM comparison c WHERE (SELECT count(*) FROM comparison_item i "
                             f"WHERE i.comparison_id=c.id) < 2 {rec_sql}", args)
    return cur.rowcount


# ---- sides --------------------------------------------------------------------------------------------------

async def _group(pool, registry, stt_run_id, group_id):
    from ..api.analyses import RUN_COLS, _assemble, group_numbers     # same grouping/retry/label rules as the analyses API
    async with pool.connection() as c:
        cur = await c.execute(f"SELECT {RUN_COLS} FROM analysis_run a LEFT JOIN output o ON o.analysis_run_id=a.id "
                              "WHERE a.stt_run_id=%s ORDER BY a.created_at, a.id", (stt_run_id,))
        rows = await cur.fetchall()
        numbers = await group_numbers(c, stt_run_id)
    return next((g for g in _assemble(rows, registry, numbers) if g["group_id"] == str(group_id)), None)


async def _hash(pool, group_id):
    async with pool.connection() as c:
        row = await (await c.execute("SELECT input_text_sha256 FROM analysis_run WHERE group_id=%s "
                                     "AND input_text_sha256 IS NOT NULL LIMIT 1", (group_id,))).fetchone()
    return row["input_text_sha256"] if row else None


async def side_info(pool, registry, side, stt_run_id, group_id):
    from . import stt_runs
    run = await stt_runs.get_run(pool, registry, stt_run_id)
    if run is None:
        return None
    group = await _group(pool, registry, stt_run_id, group_id) if group_id else None
    label = run["label"] + (f" + {group['label']}" if group else "")
    return {"side": side, "label": label, "stt_run_id": str(stt_run_id),
            "analysis_group_id": str(group_id) if group_id else None,
            "stt": {"label": run["label"], "model_id": run["model_id"], "model_label": run["model_label"],
                    "wall_ms": run["wall_ms"], "rtf": run["rtf"], "status": run["status"]},
            "ai": {"label": group["label"], "model_id": group["model_id"], "model_label": group["model_label"],
                   "wall_ms": group["wall_ms"]} if group else None,
            "date": run["finished_at"] or run["created_at"], "_group": group}


# ---- create -------------------------------------------------------------------------------------------------

def _side_in(raw, name):
    if not isinstance(raw, dict):
        raise CompareError(422, f"side {name} must be an object with stt_run_id")
    rid = library.as_uuid(raw.get("stt_run_id"))
    if rid is None:
        raise CompareError(422, f"side {name}: stt_run_id is not a valid run id")
    gid = raw.get("analysis_group_id")
    if gid is not None:
        gid = library.as_uuid(gid)
        if gid is None:
            raise CompareError(422, f"side {name}: analysis_group_id is not a valid id")
    return rid, gid


async def create(pool, registry, recording_id, a, b):
    """-> comparison id. CompareError 422 (invalid) / 404 (unknown run)."""
    rec = library.as_uuid(recording_id)
    if rec is None:
        raise CompareError(422, "recording_id is required")
    sides = [_side_in(a, "A"), _side_in(b, "B")]
    if sides[0] == sides[1]:
        raise CompareError(422, "pick two different runs to compare")
    async with pool.connection() as c:
        for name, (rid, gid) in zip("AB", sides):
            run = await (await c.execute("SELECT recording_id, status FROM stt_run WHERE id=%s", (rid,))).fetchone()
            if run is None:
                raise CompareError(404, f"side {name}: transcript run not found")
            if str(run["recording_id"]) != rec:
                raise CompareError(422, "the runs belong to different recordings")
            if run["status"] != "done":
                raise CompareError(422, f"side {name}: the transcript run is {run['status']}, not done")
            if gid is not None and await (await c.execute(
                    "SELECT 1 FROM analysis_run WHERE group_id=%s AND stt_run_id=%s", (gid, rid))).fetchone() is None:
                raise CompareError(422, f"side {name}: that analysis does not belong to its transcript run")
    infos = [await side_info(pool, registry, n, rid, gid) for n, (rid, gid) in zip("AB", sides)]
    async with pool.connection() as c:
        async with c.transaction():
            cur = await c.execute("INSERT INTO comparison (recording_id) VALUES (%s) RETURNING id", (rec,))
            cid = (await cur.fetchone())["id"]
            for info, (rid, gid) in zip(infos, sides):
                await c.execute("INSERT INTO comparison_item (comparison_id, side, stt_run_id, analysis_group_id, label)"
                                " VALUES (%s,%s,%s,%s,%s)", (cid, info["side"], rid, gid, info["label"]))
    await ensure_eval(pool, rec, [s[0] for s in sides])
    return str(cid)


async def _script(pool, recording_id):
    async with pool.connection() as c:
        return await (await c.execute("SELECT s.id, s.slug, s.name, s.sha256, s.body_md FROM recording r "
                                      "JOIN script s ON s.id=r.script_id WHERE r.id=%s", (recording_id,))).fetchone()


async def ensure_eval(pool, recording_id, run_ids):
    """Score each run against the recording's script once per (run, script hash). No script / no lines: nothing."""
    script = await _script(pool, recording_id)
    if script is None or not scoring.reference_text(script["body_md"]):
        return
    for rid in run_ids:
        async with pool.connection() as c:
            if await (await c.execute("SELECT 1 FROM eval_result WHERE stt_run_id=%s AND script_sha256=%s",
                                      (rid, script["sha256"]))).fetchone():
                continue
            text = (await (await c.execute("SELECT text FROM stt_run WHERE id=%s", (rid,))).fetchone())["text"]
        metrics = await asyncio.to_thread(scoring.score_run, script["body_md"], text)
        if metrics is None:
            continue
        async with pool.connection() as c:
            await c.execute("INSERT INTO eval_result (stt_run_id, script_id, script_sha256, metrics) "
                            "VALUES (%s,%s,%s,%s) ON CONFLICT (stt_run_id, script_sha256) DO NOTHING",
                            (rid, script["id"], script["sha256"], Jsonb(metrics)))


# ---- read ---------------------------------------------------------------------------------------------------

async def list_for(pool, recording_id):
    rec = library.as_uuid(recording_id)
    if rec is None:
        return None
    async with pool.connection() as c:
        cur = await c.execute(
            "SELECT c.id, c.created_at, ia.label AS a_label, ib.label AS b_label FROM comparison c "
            "JOIN comparison_item ia ON ia.comparison_id=c.id AND ia.side='A' "
            "JOIN comparison_item ib ON ib.comparison_id=c.id AND ib.side='B' "
            "WHERE c.recording_id=%s ORDER BY c.created_at DESC, c.id DESC", (rec,))
        return [{"id": str(r["id"]), "created_at": _plain(r["created_at"]), "a": {"label": r["a_label"]},
                 "b": {"label": r["b_label"]}} for r in await cur.fetchall()]


async def _items_of(pool, comparison_id):
    cid = library.as_uuid(comparison_id)
    if cid is None:
        return None
    async with pool.connection() as c:
        head = await (await c.execute("SELECT id, recording_id, created_at FROM comparison WHERE id=%s", (cid,))).fetchone()
        if head is None:
            return None
        cur = await c.execute("SELECT side, stt_run_id, analysis_group_id FROM comparison_item WHERE comparison_id=%s "
                              "ORDER BY side", (cid,))
        items = {r["side"]: r for r in await cur.fetchall()}
    return head, items if set(items) == {"A", "B"} else None


async def get(pool, registry, comparison_id):
    got = await _items_of(pool, comparison_id)
    if not got or got[1] is None:
        return None
    head, items = got
    infos = {s: await side_info(pool, registry, s, i["stt_run_id"], i["analysis_group_id"]) for s, i in items.items()}
    if None in infos.values():
        return None
    groups = {s: infos[s].pop("_group") for s in infos}
    outputs = {}
    for kind in KINDS:
        sides = {}
        for s in "AB":
            kd = next((k for k in (groups[s]["kinds"] if groups[s] else []) if k["kind"] == kind), None)
            if kd is None:
                sides[s.lower()] = None
                continue
            invalid = kd["schema_valid"] is False
            out = None if invalid else kd["output"]
            prompt = {"name": kd["prompt_name"], "version": kd["prompt_version"], "checks": kd["prompt_checks"]} \
                if kd["prompt_version"] is not None else None
            sides[s.lower()] = {"status": kd["status"], "invalid": invalid, "error": kd["error"],
                                "wall_ms": kd["wall_ms"], "prompt": prompt,
                                "counts": output_counts(kind, out["content"]) if out else None,
                                "content": out["content"] if out else None}
        if sides["a"] or sides["b"]:
            outputs[kind] = sides
    warnings = []
    if all(infos[s]["analysis_group_id"] for s in "AB"):
        ha, hb = await _hash(pool, infos["A"]["analysis_group_id"]), await _hash(pool, infos["B"]["analysis_group_id"])
        if ha and hb and ha != hb:
            warnings.append(dict(WARN_DIFFERENT))
    return {"id": str(head["id"]), "recording_id": str(head["recording_id"]), "created_at": _plain(head["created_at"]),
            "a": infos["A"], "b": infos["B"], "outputs": outputs, "warnings": warnings,
            "eval": await _eval(pool, head["recording_id"], infos)}


async def _eval(pool, recording_id, infos):
    script = await _script(pool, recording_id)
    if script is None:
        return None
    out = {}
    async with pool.connection() as c:
        for s in "AB":
            row = await (await c.execute("SELECT metrics FROM eval_result WHERE stt_run_id=%s AND script_sha256=%s",
                                         (infos[s]["stt_run_id"], script["sha256"]))).fetchone()
            if row is None:
                return None
            out[s.lower()] = row["metrics"]
    out["script"] = {"id": script["id"], "slug": script["slug"], "name": script["name"], "sha256": script["sha256"]}
    return out


async def transcripts(pool, comparison_id):
    got = await _items_of(pool, comparison_id)
    if not got or got[1] is None:
        return None
    out = {}
    async with pool.connection() as c:
        for s, i in got[1].items():
            row = await (await c.execute(
                "SELECT COALESCE(r.text, (SELECT string_agg(t.text, ' ' ORDER BY t.idx) FROM transcript_segment t "
                "WHERE t.stt_run_id=r.id), '') AS text FROM stt_run r WHERE r.id=%s", (i["stt_run_id"],))).fetchone()
            out[s.lower()] = {"text": row["text"] if row else ""}
    return out
