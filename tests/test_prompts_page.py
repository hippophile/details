"""prompt-ab: the /prompts page (ids, nav entry, PAGES route, no innerHTML), the Analyse version pickers, node tests."""
import asyncio, re, shutil, subprocess, sys

import pytest

from conftest import client_for
from details import app as app_mod
from details.app import create_app
from details.settings import REPO_ROOT

IDS = ("nav", "kindList", "versionList", "pTitle", "pMeta", "pBody", "editBtn", "editor", "editText", "editNote",
       "saveBtn", "cancelBtn", "saveMsg", "result")


def fetch(mk_settings, *paths):
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            out = []
            for p in paths:
                r = await c.get(p)
                assert r.status == 200, p
                out.append(await r.text())
            return out
    return asyncio.run(go())


def test_prompts_is_a_page_and_in_the_nav():
    assert "prompts" in app_mod.PAGES
    js = (REPO_ROOT / "web/shared/nav.js").read_text()
    assert '["Prompts", "/prompts"]' in js and js.index("Analyse") < js.index("Prompts")


def test_page_has_ids_assets_and_the_two_notices(mk_settings):
    html, = fetch(mk_settings, "/prompts")
    for i in IDS:
        assert f'id="{i}"' in html, i
    for needle in ("/shared/shared.css", "/shared/nav.js", "/prompts/prompts.css", "/prompts/prompts.js",
                   "Edit as new version", "aria-live", "commit", "when a run"):
        assert needle in html, needle
    assert re.search(r'<pre[^>]*id="pBody"', html) and re.search(r'<div[^>]*id="editor"[^>]*hidden', html)
    assert html.lower().count("<textarea") == 1                        # the only way to type is the new-version box
    assert not re.search(r"overwrite|delete", html, re.I)


def test_js_wiring_and_no_innerhtml(mk_settings):
    js, css = fetch(mk_settings, "/prompts/prompts.js", "/prompts/prompts.css")
    for needle in ("/api/prompts", "base_version", "textContent", "method: \"POST\""):
        assert needle in js, needle
    assert "innerHTML" not in js
    assert "var(--" in css


def test_analyse_page_loads_versions_and_sends_prompt_versions(mk_settings):
    js, = fetch(mk_settings, "/analyse/outputs.js")
    for needle in ("/api/prompts", "prompt_versions", "pickerKinds", "promptVersionsBody"):
        assert needle in js, needle
    assert "innerHTML" not in js


def test_node_prompts_outputs_and_compare_helper_tests():
    node = shutil.which("node")
    if not node:
        sys.__stderr__.write("\nSKIP: node not found, prompt-ab JS tests not run\n")
        pytest.skip("node not found")
    files = [str(REPO_ROOT / "tests" / "js" / n) for n in ("prompts.test.mjs", "outputs.test.mjs", "compare.test.mjs")]
    r = subprocess.run([node, "--test", *files], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2500:] + r.stderr[-800:]
