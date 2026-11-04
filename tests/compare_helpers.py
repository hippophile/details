"""Helpers for the compare-runs tests: a seeded script, done stt_runs with text, analysis groups with outputs."""
import uuid

from psycopg.types.json import Jsonb

from details.services import scripts
from details.settings import REPO_ROOT
from stt_helpers import insert_run, rec

STANDUP = REPO_ROOT / "db" / "seeds" / "scripts" / "01-standup.md"


def add_script(db, path=STANDUP, body=None):
    s = scripts.parse(path)
    s["slug"] = "cmp-" + s["slug"] + ("-custom" if body is not None else "")   # app start re-syncs the seeded slugs
    if body is not None:
        import hashlib
        s["body_md"], s["sha256"] = body, hashlib.sha256(body.encode()).hexdigest()
    return db.execute("INSERT INTO script (slug, name, body_md, sha256, sort_order) VALUES (%(slug)s, %(name)s, "
                      "%(body_md)s, %(sha256)s, %(sort_order)s) RETURNING id", s).fetchone()[0], s


def seed_rec(db, settings, script_id=None, title="rec"):
    rid = rec(db, settings, seconds=5, title=title)
    if script_id:
        db.execute("UPDATE recording SET script_id=%s WHERE id=%s", (script_id, rid))
    return rid


def seed_stt(db, rid, text="καλημέρα σε όλους", model_id="whisper-turbo", wall_ms=4200, rtf=7.1, status="done"):
    return insert_run(db, rid, status=status, model_id=model_id, text=text, wall_ms=wall_ms, audio_ms=30000, rtf=rtf)


def good_checklist(n=5, done=2):
    return {"schema_version": 1, "title": "t", "items": [
        {"text": f"i{i}", "owner": None, "due": None, "done": i < done, "quote": "q"} for i in range(n)]}


def seed_analysis(db, stt_run_id, outputs=None, group_id=None, model_id="gemma-cheap", sha="h1", wall_ms=12000,
                  valid=None):
    """One analysis group: a done analysis_run (+ output) per kind. valid: {kind: bool} to override schema_valid."""
    gid = group_id or str(uuid.uuid4())
    valid = valid or {}
    for kind, content in (outputs or {"checklist": good_checklist()}).items():
        ok = valid.get(kind, True)
        ar = db.execute("INSERT INTO analysis_run (group_id, stt_run_id, kind, model_id, status, wall_ms, schema_valid, "
                        "input_text_sha256, params) VALUES (%s,%s,%s,%s,'done',%s,%s,%s,%s) RETURNING id",
                        (gid, stt_run_id, kind, model_id, wall_ms, ok, sha, Jsonb({}))).fetchone()[0]
        if ok:
            db.execute("INSERT INTO output (analysis_run_id, kind, schema_version, content) VALUES (%s,%s,1,%s)",
                       (ar, kind, Jsonb(content)))
    return gid


def side(stt_run_id, group=None):
    d = {"stt_run_id": stt_run_id}
    if group:
        d["analysis_group_id"] = group
    return d


def body(rid, a, b):
    return {"recording_id": rid, "a": a, "b": b}
