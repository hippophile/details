"""speakers-diarization: the Who talked card on /analyse (ids, assets, textContent only, no model ids) + JS unit tests."""
import asyncio, re, shutil, subprocess, sys

import pytest

from conftest import client_for
from details.app import create_app
from details.settings import REPO_ROOT

IDS = ("spkCard", "spkMin", "spkMax", "spkStart", "spkReason", "spkGpuHint", "spkRun", "spkBars", "spkTimeline",
       "spkTurns", "spkNames", "spkWarn1", "spkWarn2", "spkEmpty", "spkStatus", "aiLabels")
OLD_IDS = ("player", "transcript", "aiSlot", "progress", "stage", "pct", "runs")


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


def test_page_has_the_card_ids_keeps_the_old_ones_and_loads_the_assets(mk_settings):
    html, = fetch(mk_settings, "/analyse")
    for i in IDS + OLD_IDS:
        assert f'id="{i}"' in html, i
    assert "/analyse/speakers.js" in html and "/analyse/speakers.css" in html
    assert html.index("/analyse/api.js") < html.index("/analyse/speakers.js") < html.index("/analyse/analyse.js")


def test_both_warning_lines_the_gpu_hint_and_the_select_label_are_on_the_page(mk_settings):
    html, = fetch(mk_settings, "/analyse")
    text = re.sub(r"\s+", " ", html)
    assert "Speaker labels are a guess. Check before sharing." in text
    assert "each transcript line gets one speaker" in text.lower() and "talk-time percentages" in text
    assert "Live assist on the Record page waits while speakers are being detected." in text
    assert "Who talked" in text and "Speaker labels" in text and "Detect speakers" in text


def test_js_uses_the_diar_endpoints_and_textcontent_without_innerhtml(mk_settings):
    js, = fetch(mk_settings, "/analyse/speakers.js")
    for needle in ("/api/models?kind=diar", "/diarization-runs", "/turns", "/speakers/", "textContent", "PATCH",
                   "AnalyseJobs", "unavailable_reason", "seekTo", "diarization_run_id"):
        assert needle in js, needle
    for bad in ("innerHTML", "insertAdjacentHTML", "document.write"):
        assert bad not in js


def test_no_model_ids_in_the_page_code(mk_settings):
    for text in fetch(mk_settings, "/analyse", "/analyse/speakers.js", "/analyse/speakers.css", "/analyse/analyse.js"):
        assert not re.search(r"community-1|pyannote|whisper-(turbo|large)|mlx-community|gemma-(cheap|nice)", text)


def test_css_defines_six_colours_dark_mode_and_the_unknown_class(mk_settings):
    css, = fetch(mk_settings, "/analyse/speakers.css")
    for n in range(0, 7):
        assert f".spk-c{n}" in css, n
    assert "prefers-color-scheme: dark" in css


def test_analyse_js_exports_the_job_watcher_and_asks_for_labelled_segments(mk_settings):
    js, outputs = fetch(mk_settings, "/analyse/analyse.js", "/analyse/outputs.js")
    assert "window.AnalyseJobs" in js and "onFinish" in js and "diarization_run_id" in js
    assert "diarization_run_id" in outputs and "innerHTML" not in outputs


def test_node_speakers_unit_tests():
    node = shutil.which("node")
    if not node:
        sys.__stderr__.write("\nSKIP: node not found (speakers JS helper tests not run)\n")
        pytest.skip("node not found")
    r = subprocess.run([node, "--test", str(REPO_ROOT / "tests" / "js" / "speakers.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]


def test_setup_steps_sit_in_a_closed_disclosure_and_spkreason_stays_the_one_line_notice(mk_settings):
    html, js = fetch(mk_settings, "/analyse", "/analyse/speakers.js")
    text = re.sub(r"\s+", " ", html)
    assert 'id="spkSetup"' in text and "<summary>How to set it up</summary>" in text and 'id="spkSetupText"' in text
    assert re.search(r'<details id="spkSetup"(?![^>]*\bopen)', text)
    assert 'id="spkReason"' in text and "setupNotice" in js and "spkSetupText" in js
