"""llm-outputs: prompt templates as git files (sync, edit = new version), rendering, output language."""
import asyncio, hashlib

import pytest

from details.db import open_pool
from details.llm import prompts
from details.llm.schemas import KINDS
from details.settings import REPO_ROOT

FRONT = ("---\nname: {name}\nversion: {version}\nkind: {kind}\noutput_schema: {kind}.v1\ndescription: t\n"
         "suggested_params: {{\"temperature\": 0.1}}\n---\n")


def write(root, kind, version, body="Write in {{language}}.\n{{transcript}}\nTitle: {{recording_title}}\n", name=None):
    d = root / kind
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"v{version}.md"
    f.write_text(FRONT.format(name=name or kind, version=version, kind=kind) + body)
    return f


def sync(db_url, root):
    async def go():
        pool = await open_pool(db_url)
        try:
            await prompts.sync_prompts(pool, root)
        finally:
            await pool.close()
    asyncio.run(go())


def rows(db):
    return db.execute("SELECT name, version, kind, sha256 FROM prompt_template ORDER BY name, version").fetchall()


def test_new_file_is_inserted_with_its_sha256(db, db_url, tmp_path):
    f = write(tmp_path, "debrief", 1)
    sync(db_url, tmp_path)
    assert rows(db) == [("debrief", 1, "debrief", hashlib.sha256(f.read_bytes()).hexdigest())]
    sync(db_url, tmp_path)   # a second sync is a no-op
    assert len(rows(db)) == 1


def test_edited_version_is_refused_and_row_unchanged(db, db_url, tmp_path):
    f = write(tmp_path, "debrief", 1)
    sync(db_url, tmp_path)
    before = rows(db)
    f.write_text(f.read_text() + "extra line\n")
    with pytest.raises(prompts.PromptError, match=r"debrief.*v1.*v2\.md"):
        sync(db_url, tmp_path)
    assert rows(db) == before


def test_unknown_placeholder_names_it(db, db_url, tmp_path):
    write(tmp_path, "debrief", 1, body="Speakers: {{speakers}}\n{{transcript}}\n")
    with pytest.raises(prompts.PromptError, match=r"\{\{speakers\}\}"):
        sync(db_url, tmp_path)
    assert rows(db) == []


def test_new_version_file_is_added_next_to_the_old(db, db_url, tmp_path):
    write(tmp_path, "tags", 1)
    sync(db_url, tmp_path)
    write(tmp_path, "tags", 2, body="Other {{transcript}}\n")
    sync(db_url, tmp_path)
    assert [(r[0], r[1]) for r in rows(db)] == [("tags", 1), ("tags", 2)]

    async def latest():
        pool = await open_pool(db_url)
        try:
            return await prompts.latest_templates(pool)
        finally:
            await pool.close()
    assert asyncio.run(latest())["tags"].version == 2


def test_shipped_prompts_parse_and_sync(db, db_url):
    sync(db_url, REPO_ROOT / "prompts")
    assert {r[0] for r in rows(db)} == set(KINDS)
    for kind in KINDS:
        t = prompts.parse_template(REPO_ROOT / "prompts" / kind / "v1.md")
        assert t.kind == kind and t.version == 1 and "{{transcript}}" in t.body
        assert "{{language}}" in t.body and "verbatim" in t.body.lower()


def test_render_replaces_only_the_three_placeholders():
    out = prompts.render("L={{language}} T={{recording_title}} X={{transcript}}", "τρανσκρίπτ", "Greek", "Τίτλος")
    assert out == "L=Greek T=Τίτλος X=τρανσκρίπτ"


def test_language_follows_the_run_and_the_english_switch():
    assert prompts.language_name("el", "any text") == "Greek"
    assert prompts.language_name("el", "any text", "en") == "English"
    assert prompts.language_name("en", "καλημέρα") == "English"


def test_auto_language_uses_greek_letter_share():
    assert prompts.language_name(None, "Καλημέρα σε όλους, ξεκινάμε τη συνάντηση. ok") == "Greek"
    assert prompts.language_name("auto", "We will deploy on Friday, with a few Ελληνικά words") == "English"


def test_shipped_brief_prompt_lists_known_scripts_and_the_not_a_meeting_rule():
    t = prompts.parse_template(REPO_ROOT / "prompts" / "brief" / "v1.md")
    assert (t.kind, t.version, t.output_schema, t.checks) == ("brief", 1, "brief.v1", ("known_scripts",))
    body = t.body.lower()
    for needle in ("kind_of_recording", "lesson", "empty list", "agreed", "verbatim", "{{recording_title}}"):
        assert needle in body, needle
    assert KINDS[0] == "brief"
