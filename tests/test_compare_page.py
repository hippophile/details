"""compare-runs: page wiring (element ids, assets, no innerHTML, no hardcoded models) + node tests with a visible SKIP."""
import asyncio, re, shutil, subprocess, sys

import pytest

from conftest import client_for
from details.app import create_app
from details.settings import REPO_ROOT

IDS = ("compareBtn", "compareHint", "compareView", "cmpHeaderA", "cmpHeaderB", "cmpTabs", "cmpWarnings", "cmpPanes",
       "cmpSwap", "cmpClose", "cmpProgress", "cmpEval", "cmpToggle")


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


def test_page_has_compare_ids_and_assets(mk_settings):
    html, = fetch(mk_settings, "/analyse")
    for i in IDS:
        assert f'id="{i}"' in html, i
    for needle in ("/analyse/compare.css", "/analyse/compare.js"):
        assert needle in html, needle
    assert re.search(r'<button[^>]*id="compareBtn"[^>]*disabled', html)
    assert "measured on this Mac" in html and "/analyse/compare.js" in html


def test_js_and_css_wiring(mk_settings):
    cjs, djs, worker, css, ajs = fetch(mk_settings, "/analyse/compare.js", "/analyse/diff.js",
                                       "/analyse/diff-worker.js", "/analyse/compare.css", "/analyse/analyse.js")
    for needle in ("/api/comparisons", "compare=", "new Worker", "requestAnimationFrame", "data.warnings",
                   "no script reference for this recording", "invalid output", "no outputs", "no AI step",
                   "coarse diff", "textContent"):
        assert needle in cjs, needle
    assert "innerHTML" not in cjs + djs + worker
    assert "type: \"module\"" in cjs and "import" in worker and "export" in djs
    for needle in ("max-width", "700px", "line-through", "underline"):
        assert needle in css, needle
    assert "Compare" in ajs and "AnalyseCompare" in ajs
    assert not re.search(r"whisper-(turbo|large)|mlx-community", cjs + css)


def test_node_diff_and_compare_helper_tests():
    node = shutil.which("node")
    if not node:
        sys.__stderr__.write("\nSKIP: node not found, JS diff tests not run\n")
        pytest.skip("node not found, JS diff tests not run")
    files = [str(REPO_ROOT / "tests" / "js" / n) for n in ("diff.test.mjs", "compare.test.mjs")]
    r = subprocess.run([node, "--test", *files], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2500:] + r.stderr[-800:]
