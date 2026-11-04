"""run-insights UI: ids, textContent-only, no hardcoded model ids, JS unit tests (SKIP is visible without node)."""
import asyncio, re, shutil, subprocess, sys

import pytest

from conftest import client_for
from details.app import create_app
from details.settings import REPO_ROOT

IDS = ("stepTable", "stepRows", "logPanel", "logBody", "logLevel", "logDownload", "inspectDlg", "inspectTabs",
       "inspectBody", "inspectCopy", "inspectAttempts")
OLD_IDS = ("progress", "stage", "pct", "elapsed", "cancel", "barfill", "aiStatus", "aiFooter", "aiTabs", "aiPanel")


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


def test_page_has_new_ids_keeps_old_ones_and_loads_assets(mk_settings):
    html, = fetch(mk_settings, "/analyse")
    for i in IDS + OLD_IDS:
        assert f'id="{i}"' in html, i
    for needle in ("/analyse/insights.js", "/analyse/insights.css"):
        assert needle in html, needle
    assert html.index("/analyse/api.js") < html.index("/analyse/insights.js") < html.index("/analyse/analyse.js")
    assert html.index("/analyse/outputs.js") < html.index("/analyse/analyse.js")


def test_assets_are_served_textcontent_only_without_model_ids(mk_settings):
    js, css, outputs = fetch(mk_settings, "/analyse/insights.js", "/analyse/insights.css", "/analyse/outputs.js")
    for text in (js, outputs):
        assert "innerHTML" not in text and "insertAdjacentHTML" not in text and "document.write" not in text
    for needle in ("textContent", "/api/jobs/", "/log", "after=", "download=1", "setInterval", "/inspect",
                   "analysis-runs", "clipboard", "scroll"):
        assert needle in js, needle
    assert "Inspect" in outputs and "Insights" in outputs


def test_model_ids_are_not_hardcoded_in_the_page_js(mk_settings):
    js, = fetch(mk_settings, "/analyse/insights.js")
    assert not re.search(r"whisper-(turbo|large)|mlx-community|gemma-(cheap|nice)", js)


def test_node_insights_unit_tests():
    node = shutil.which("node")
    if not node:
        sys.__stderr__.write("\nSKIP: node not found (insights JS helper tests not run)\n")
        pytest.skip("node not found")
    r = subprocess.run([node, "--test", str(REPO_ROOT / "tests" / "js" / "insights.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]
