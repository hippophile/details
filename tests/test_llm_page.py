"""llm-outputs: the AI model card on the Analyse page (element ids, no forced default, no innerHTML) + node helpers."""
import asyncio, re, shutil, subprocess, sys

import pytest

from conftest import client_for
from details.app import create_app
from details.settings import REPO_ROOT

IDS = ("aiSlot", "aiSource", "aiModel", "aiLang", "aiStart", "aiReason", "aiStatus", "aiResults", "aiTabs", "aiPanel",
       "aiFooter", "aiCopy", "aiDownload", "aiCopyAll", "aiDownloadAll", "aiMsg")
KINDS = ("debrief", "notes", "checklist", "tags")


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


def test_card_ids_chips_and_no_preselected_model(mk_settings):
    html, = fetch(mk_settings, "/analyse")
    for i in IDS:
        assert f'id="{i}"' in html, i
    for k in KINDS:
        assert re.search(rf'<input[^>]*data-kind="{k}"[^>]*checked', html), k
    assert "/analyse/outputs.js" in html and "/analyse/outputs.css" in html
    assert re.search(r'<button[^>]*id="aiStart"[^>]*disabled', html)
    sel = re.search(r'<select id="aiModel".*?</select>', html, re.S).group(0)
    assert "selected" not in sel


def test_js_wiring(mk_settings):
    js, css = fetch(mk_settings, "/analyse/outputs.js", "/analyse/outputs.css")
    for needle in ("/api/models?kind=llm", "Choose AI model", "/analyses", "/api/analyses/", "PATCH", "/items/",
                   "/markdown", "download=1", "type=checkbox".replace("=", "\" === \"") if False else "checkbox",
                   "unverified", "model returned invalid JSON", "textContent", "clipboard", "unavailable_reason",
                   "needs_gb", "role", "output_language"):
        assert needle in js, needle
    assert "innerHTML" not in js and not re.search(r"gemma", js + css, re.I)
    assert ".unverified" in css


def test_analyse_js_hooks_outputs(mk_settings):
    js, = fetch(mk_settings, "/analyse/analyse.js")
    assert "Outputs" in js and "innerHTML" not in js


def test_node_unit_tests_for_outputs_helpers():
    node = shutil.which("node")
    if not node:
        sys.__stderr__.write("\nSKIP: node not found (outputs JS helper tests not run)\n")
        pytest.skip("node not found")
    r = subprocess.run([node, "--test", str(REPO_ROOT / "tests" / "js" / "outputs.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]
