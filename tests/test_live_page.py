import asyncio, re, shutil, subprocess, sys

import pytest

from conftest import client_for
from details.app import create_app
from details.settings import REPO_ROOT


def get(mk_settings, path):
    async def go():
        async with client_for(create_app(mk_settings())) as c:
            r = await c.get(path)
            return r.status, await r.text()
    return asyncio.run(go())


def test_record_page_has_the_toggle_the_card_and_the_v1_link(mk_settings):
    status, t = get(mk_settings, "/record")
    assert status == 200
    for needle in ('id="liveToggle"', 'type="checkbox"', 'id="live"', 'href="/live"', "/record/live.js",
                   "/record/live.css", 'id="liveStatus"', 'aria-live="polite"', 'id="liveLines"', 'id="livePartial"',
                   'id="liveNotes"', 'id="liveActions"', 'id="liveTags"'):
        assert needle in t, needle
    assert not re.search(r'id="liveToggle"[^>]*\bchecked\b', t)            # off by default
    assert re.search(r'id="live"[^>]*\bhidden\b', t)                      # card hidden until toggled
    assert t.count('aria-live="polite"') == 1                             # the status line only


def test_live_assets_are_served_and_use_textcontent_only(mk_settings):
    for f in ("live.js", "live.css"):
        assert get(mk_settings, "/record/" + f)[0] == 200
    js = get(mk_settings, "/record/live.js")[1]
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js
    rec = get(mk_settings, "/record/record.js")[1]
    assert "innerHTML" not in rec and "live_" in rec and "silence_db" in rec and "resampl" not in rec.lower()


def test_v1_live_page_still_served(mk_settings):
    assert get(mk_settings, "/live")[0] == 200


def test_node_live_tests():
    node = shutil.which("node")
    if not node:
        sys.__stderr__.write("\nSKIP: node not found (live JS tests not run)\n")
        pytest.skip("node not found")
    r = subprocess.run([node, "--test", str(REPO_ROOT / "tests" / "js" / "live.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-1800:] + r.stderr[-500:]
