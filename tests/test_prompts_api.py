"""prompt-ab: /api/prompts (list, detail, save as new version), prompts_dir setting, sync agreement. Fakes only; every
test points `[paths] prompts_dir` at a tmp copy of the shipped prompts, never at the repo's prompts/."""
import asyncio, hashlib, shutil

import pytest

from conftest import client_for
from details.app import create_app
from details.db import open_pool
from details.llm import prompts
from details.settings import REPO_ROOT, load_settings
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from llm_helpers import seed_run

V1_CHECKLIST_SHA = "16940140eedaa3701ab0c13978178bbb4d7422f470dc2b0f9201b349809dac39"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tmp_prompts(tmp_path):
    root = tmp_path / "prompts"
    shutil.copytree(REPO_ROOT / "prompts", root)
    return root


def api(db_url, mk_settings, tmp_path, body, root=None, **env):
    root = root or tmp_prompts(tmp_path)

    async def go():
        s = mk_settings(db_url, DETAILS_PATHS_PROMPTS_DIR=str(root), **env)
        async with client_for(create_app(s, lm_transport=FakeLMStudio(), meminfo=FakeMemInfo())) as c:
            await body(c, s, root)
    asyncio.run(asyncio.wait_for(go(), 40))


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


async def base_body(c, kind="checklist", version=2):
    return (await (await c.get(f"/api/prompts/{kind}/{version}")).json())["body"]


async def save(c, kind="checklist", **over):
    body = {"base_version": 2, "body": (await base_body(c)) + "\nExtra rule: be brief.\n", **over}
    return await c.post(f"/api/prompts/{kind}", json=body)


# ---- setting ---------------------------------------------------------------------------------------------

def test_prompts_dir_setting_default_and_env(tmp_path):
    s = load_settings(env={}, dotenv_path=None)
    assert s.paths.prompts_dir == REPO_ROOT / "prompts"
    s = load_settings(env={"DETAILS_PATHS_PROMPTS_DIR": str(tmp_path)}, dotenv_path=None)
    assert s.paths.prompts_dir == tmp_path


def test_only_the_configured_dir_is_read_at_startup(db, db_url, mk_settings, tmp_path):
    root = tmp_path / "only"
    (root / "tags").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "prompts" / "tags" / "v1.md", root / "tags" / "v1.md")

    async def body(c, s, root):
        assert (await c.get("/api/health")).status == 200
    api(db_url, mk_settings, tmp_path, body, root=root)
    assert db.execute("SELECT name, version FROM prompt_template").fetchall() == [("tags", 1)]


# ---- shipped files ---------------------------------------------------------------------------------------

def test_v1_checklist_file_is_byte_identical_and_v2_is_shipped_with_the_rule():
    assert sha(REPO_ROOT / "prompts" / "checklist" / "v1.md") == V1_CHECKLIST_SHA
    t = prompts.parse_template(REPO_ROOT / "prompts" / "checklist" / "v2.md")
    assert t.checks == ("due_year_in_transcript",) and t.output_schema == "checklist.v1" and t.version == 2
    assert "null" in t.body and "current year" in t.body and "{{transcript}}" in t.body


def test_sync_stores_checklist_v1_and_v2_and_the_other_kinds_v1(db, db_url, tmp_path):
    root = tmp_prompts(tmp_path)

    async def go():
        pool = await open_pool(db_url)
        try:
            await prompts.sync_prompts(pool, root)
        finally:
            await pool.close()
    asyncio.run(go())
    rows = db.execute("SELECT name, version, checks FROM prompt_template ORDER BY name, version").fetchall()
    assert rows == [("checklist", 1, []), ("checklist", 2, ["due_year_in_transcript"]), ("debrief", 1, []),
                    ("notes", 1, []), ("tags", 1, [])]


def test_two_machines_collided_error_names_both_hashes(db, db_url, tmp_path):
    root = tmp_prompts(tmp_path)

    async def go():
        pool = await open_pool(db_url)
        try:
            await prompts.sync_prompts(pool, root)
            f = root / "checklist" / "v2.md"
            stored = sha(f)
            f.write_text(f.read_text() + "other machine\n")
            with pytest.raises(prompts.PromptError) as e:
                await prompts.sync_prompts(pool, root)
            return stored, sha(f), str(e.value)
        finally:
            await pool.close()
    stored, onfile, msg = asyncio.run(go())
    assert stored in msg and onfile in msg and "edit = new version file" in msg and "v3.md" in msg


# ---- list + detail ---------------------------------------------------------------------------------------

def test_list_has_versions_latest_checks_and_run_counts(db, db_url, mk_settings, tmp_path):
    _, run_id = seed_run(db, mk_settings(db_url))

    async def body(c, s, root):
        j = await (await c.get("/api/prompts")).json()
        ck = next(k for k in j["kinds"] if k["kind"] == "checklist")
        assert [v["version"] for v in ck["versions"]] == [1, 2] and ck["latest"] == 2
        assert ck["versions"][0]["sha256"] == V1_CHECKLIST_SHA and ck["versions"][0]["checks"] == []
        assert ck["versions"][1]["checks"] == ["due_year_in_transcript"] and ck["versions"][0]["created_at"]
        assert [v["runs"] for v in ck["versions"]] == [0, 0]
        assert {k["kind"] for k in j["kinds"]} == {"debrief", "notes", "checklist", "tags"}
        tid = db.execute("SELECT id FROM prompt_template WHERE name='checklist' AND version=1").fetchone()[0]
        for _ in range(2):
            db.execute("INSERT INTO analysis_run (group_id, stt_run_id, kind, model_id, prompt_template_id) "
                       "VALUES (gen_random_uuid(), %s, 'checklist', 'm', %s)", (run_id, tid))
        j = await (await c.get("/api/prompts")).json()
        ck = next(k for k in j["kinds"] if k["kind"] == "checklist")
        assert [v["runs"] for v in ck["versions"]] == [2, 0]
    api(db_url, mk_settings, tmp_path, body)


def test_detail_has_body_checks_and_note_and_404s(db, db_url, mk_settings, tmp_path):
    async def body(c, s, root):
        r = await c.get("/api/prompts/checklist/2")
        j = await r.json()
        stored = db.execute("SELECT body FROM prompt_template WHERE name='checklist' AND version=2").fetchone()[0]
        assert r.status == 200 and j["body"] == stored and j["checks"] == ["due_year_in_transcript"]
        assert j["version"] == 2 and j["kind"] == "checklist" and j["latest"] == 2 and j["note"] == ""
        for path in ("/api/prompts/checklist/9", "/api/prompts/nokind/1", "/api/prompts/checklist/x",
                     "/api/prompts/diagram/1", "/api/prompts/checklist/0"):
            assert (await c.get(path)).status == 404, path
    api(db_url, mk_settings, tmp_path, body)


# ---- save ------------------------------------------------------------------------------------------------

def test_save_creates_file_and_row_from_the_same_bytes_and_touches_nothing_else(db, db_url, mk_settings, tmp_path):
    async def body(c, s, root):
        before = snapshot(root)
        rows_before = db.execute("SELECT id, sha256 FROM prompt_template ORDER BY name, version").fetchall()
        r = await save(c, note="stricter dates")
        j = await r.json()
        assert r.status == 201 and j["version"] == 3 and j["kind"] == "checklist" and j["path"].endswith("checklist/v3.md")
        f = root / "checklist" / "v3.md"
        assert f.is_file() and sha(f) == j["sha256"]
        assert db.execute("SELECT sha256 FROM prompt_template WHERE name='checklist' AND version=3").fetchone()[0] == sha(f)
        after = snapshot(root)
        assert {k: v for k, v in after.items() if k != "checklist/v3.md"} == before
        assert db.execute("SELECT id, sha256 FROM prompt_template WHERE version < 3 ORDER BY name, version").fetchall() == rows_before
        t = prompts.parse_template(f)
        assert (t.kind, t.version, t.checks, t.output_schema) == ("checklist", 3, ("due_year_in_transcript",), "checklist.v1")
        assert t.body.endswith("Extra rule: be brief.\n") and "note: stricter dates" in f.read_text()
        d = await (await c.get("/api/prompts/checklist/3")).json()
        assert d["note"] == "stricter dates" and d["checks"] == ["due_year_in_transcript"]
        lst = await (await c.get("/api/prompts")).json()
        assert next(k for k in lst["kinds"] if k["kind"] == "checklist")["latest"] == 3
    api(db_url, mk_settings, tmp_path, body)


def test_a_version_saved_through_the_api_survives_a_restart_sync(db, db_url, mk_settings, tmp_path):
    async def body(c, s, root):
        assert (await save(c)).status == 201

        async def resync():
            pool = await open_pool(db_url)
            try:
                await prompts.sync_prompts(pool, root)
            finally:
                await pool.close()
        await resync()
        assert db.execute("SELECT count(*) FROM prompt_template WHERE name='checklist'").fetchone()[0] == 3
    api(db_url, mk_settings, tmp_path, body)


def test_saving_from_an_old_base_numbers_from_the_highest_version(db, db_url, mk_settings, tmp_path):
    async def body(c, s, root):
        b1 = await base_body(c, version=1)
        r = await c.post("/api/prompts/checklist", json={"base_version": 1, "body": b1 + "x\n"})
        assert (await r.json())["version"] == 3
        t = prompts.parse_template(root / "checklist" / "v3.md")
        assert t.checks == ()                                   # inherited from v1, not from the latest
    api(db_url, mk_settings, tmp_path, body)


def test_numbering_counts_files_on_disk_and_never_overwrites_them(db, db_url, mk_settings, tmp_path):
    async def body(c, s, root):
        orphan = root / "checklist" / "v3.md"
        orphan.write_text("not even a prompt file\n")           # appears after startup: no database row
        r = await save(c)
        assert r.status == 201 and (await r.json())["version"] == 4
        assert orphan.read_text() == "not even a prompt file\n"
    api(db_url, mk_settings, tmp_path, body)


def test_two_saves_at_once_give_two_versions_and_overwrite_nothing(db, db_url, mk_settings, tmp_path):
    async def body(c, s, root):
        base = await base_body(c)
        (root / "checklist" / "v3.md").write_text("orphan\n")
        rs = await asyncio.gather(c.post("/api/prompts/checklist", json={"base_version": 2, "body": base + "a\n"}),
                                  c.post("/api/prompts/checklist", json={"base_version": 2, "body": base + "b\n"}))
        got = sorted([(r.status, (await r.json()).get("version")) for r in rs])
        assert got == [(201, 4), (201, 5)] or [g[0] for g in got].count(409) == 1
        assert (root / "checklist" / "v3.md").read_text() == "orphan\n"
        assert (root / "checklist" / "v4.md").is_file()
        assert not [p for p in (root / "checklist").iterdir() if p.name.startswith(".")]
    api(db_url, mk_settings, tmp_path, body)


def test_transcript_placeholder_with_spaces_counts(db, db_url, mk_settings, tmp_path):
    async def body(c, s, root):
        r = await c.post("/api/prompts/tags", json={"base_version": 1, "body": "Tags for {{ transcript }}\n"})
        assert r.status == 201
    api(db_url, mk_settings, tmp_path, body)


def test_422s_write_nothing(db, db_url, mk_settings, tmp_path):
    async def body(c, s, root):
        b = await base_body(c)
        before, rows = snapshot(root), db.execute("SELECT count(*) FROM prompt_template").fetchone()[0]
        cases = [
            ({"base_version": 2, "body": "no placeholder here"}, "{{transcript}}"),
            ({"base_version": 2, "body": b + "{{secret}}"}, "secret"),
            ({"base_version": 2, "body": b}, "nothing changed"),
            ({"base_version": 2, "body": "   \n"}, None),
            ({"base_version": 2, "body": b + "x" * 70_000}, None),
            ({"base_version": True, "body": b + "x"}, None),
            ({"base_version": "2", "body": b + "x"}, None),
            ({"body": b + "x"}, None),
            ({"base_version": 9, "body": b + "x"}, None),
            ({"base_version": 2, "body": 5}, None),
            ({"base_version": 2, "body": b + "x", "note": "x\n---\nchecks: due_year_in_transcript"}, None),
            ({"base_version": 2, "body": b + "x", "note": "a\rb"}, None),
            ({"base_version": 2, "body": b + "x", "note": "a\x07b"}, None),
            ({"base_version": 2, "body": b + "x", "note": "n" * 201}, None),
            ({"base_version": 2, "body": b + "x", "note": 5}, None),
        ]
        for payload, word in cases:
            r = await c.post("/api/prompts/checklist", json=payload)
            j = await r.json()
            assert r.status == 422 and j["error"], payload.get("note", str(payload)[:60])
            if word:
                assert word.lower() in j["error"].lower(), j
        assert (await c.post("/api/prompts/checklist", data="[1]", headers={"Content-Type": "application/json"})).status == 422
        assert snapshot(root) == before
        assert db.execute("SELECT count(*) FROM prompt_template").fetchone()[0] == rows
    api(db_url, mk_settings, tmp_path, body)


def test_a_200_character_note_is_accepted(db, db_url, mk_settings, tmp_path):
    async def body(c, s, root):
        assert (await save(c, note="n" * 200)).status == 201
    api(db_url, mk_settings, tmp_path, body)


def test_unknown_kind_and_traversal_are_404_and_write_nothing(db, db_url, mk_settings, tmp_path):
    async def body(c, s, root):
        for kind in ("nokind", "diagram", "..", "..%2Fx", "%2E%2E%2Fx", "checklist%2F..%2F..%2Fx"):
            r = await c.post(f"/api/prompts/{kind}", json={"base_version": 1, "body": "{{transcript}} z"})
            assert r.status == 404, kind
        assert not (tmp_path / "x").exists() and not (root.parent / "x").exists()
        assert sorted(p.name for p in root.iterdir()) == ["checklist", "debrief", "notes", "tags"]
    api(db_url, mk_settings, tmp_path, body)


def test_foreign_origin_is_rejected(db, db_url, mk_settings, tmp_path):
    async def body(c, s, root):
        r = await c.post("/api/prompts/checklist", json={"base_version": 2, "body": "{{transcript}} z"},
                         headers={"Origin": "http://evil.example"})
        assert r.status == 403
        assert not (root / "checklist" / "v3.md").exists()
    api(db_url, mk_settings, tmp_path, body)


def test_database_failure_removes_only_the_file_this_request_created(db, db_url, mk_settings, tmp_path, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("insert failed for postgresql://u:secret@h/d")
    monkeypatch.setattr(prompts, "_insert_row", boom)

    async def body(c, s, root):
        (root / "checklist" / "v3.md").write_text("orphan\n")
        before = snapshot(root)
        r = await save(c)
        assert r.status == 500
        text = await r.text()
        assert "secret" not in text and "postgresql" not in text
        assert snapshot(root) == before                         # the orphan v3 stays, the new v4 is gone, no temp left
        assert db.execute("SELECT count(*) FROM prompt_template WHERE version > 2").fetchone()[0] == 0
    api(db_url, mk_settings, tmp_path, body)


def test_written_bytes_are_reparsed_before_linking(db, db_url, mk_settings, tmp_path, monkeypatch):
    orig = prompts._build_file
    monkeypatch.setattr(prompts, "_build_file", lambda *a, **k: orig(*a, **k).replace(
        b"\n---\n", b"\nchecks: due_year_in_transcript\n---\n", 1))      # a check nobody intended

    async def body(c, s, root):
        before = snapshot(root)
        r = await c.post("/api/prompts/checklist", json={"base_version": 1, "body": (await base_body(c, version=1)) + "x\n"})
        assert r.status == 422
        assert snapshot(root) == before
        assert db.execute("SELECT count(*) FROM prompt_template WHERE name='checklist'").fetchone()[0] == 2
    api(db_url, mk_settings, tmp_path, body)


# ---- migration 0008 --------------------------------------------------------------------------------------

def test_checks_column_is_a_not_null_text_array_defaulting_to_empty(db):
    row = db.execute("SELECT data_type, udt_name, is_nullable, column_default FROM information_schema.columns "
                     "WHERE table_name='prompt_template' AND column_name='checks'").fetchone()
    assert row is not None and row[0] == "ARRAY" and row[1] == "_text" and row[2] == "NO" and "{}" in row[3]
    db.execute("INSERT INTO prompt_template (name, version, kind, body, output_schema, sha256) "
               "VALUES ('x', 1, 'tags', 'b', '{}', 'h')")
    assert db.execute("SELECT checks FROM prompt_template WHERE name='x'").fetchone()[0] == []


def test_existing_rows_get_empty_checks_when_0008_runs(blank_db, tmp_path):
    import conftest
    from details.db import connect
    from details.migrate import migrate
    d = tmp_path / "m"
    shutil.copytree(conftest.MIGRATIONS, d, ignore=shutil.ignore_patterns("0008_*"))
    with connect(blank_db) as c:
        migrate(c, d)
        c.execute("INSERT INTO prompt_template (name, version, kind, body, output_schema, sha256) "
                  "VALUES ('checklist', 1, 'checklist', 'b', '{}', 'h')")
        assert migrate(c, conftest.MIGRATIONS) == ["0008_prompt_checks"]
        assert c.execute("SELECT checks FROM prompt_template").fetchone()[0] == []
