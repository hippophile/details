import asyncio, re

from conftest import client_for
from details.app import create_app

IDS = ("recHeader", "player", "noRec", "newRun", "model", "lang", "hint", "start", "reason", "progress", "stage",
       "pct", "elapsed", "cancel", "also", "runs", "transcript", "aiSlot", "nav")


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


def test_page_served_with_expected_ids_and_assets(mk_settings):
    html, = fetch(mk_settings, "/analyse")
    for i in IDS:
        assert f'id="{i}"' in html, i
    for needle in ("<audio", "controls", "/shared/shared.css", "/shared/nav.js", "/analyse/analyse.css",
                   "/analyse/api.js", "/analyse/analyse.js", "/library", "Pick a recording in Library",
                   'aria-live="polite"'):
        assert needle in html, needle


def test_no_hardcoded_model_ids_anywhere(mk_settings):
    for text in fetch(mk_settings, "/analyse", "/analyse/analyse.js", "/analyse/api.js", "/analyse/analyse.css"):
        assert not re.search(r"whisper-(turbo|large)|mlx-community", text)


def test_also_spoken_checkboxes_come_from_the_models_api_and_no_language_code_is_in_the_script(mk_settings):
    html, js, api = fetch(mk_settings, "/analyse", "/analyse/analyse.js", "/analyse/api.js")
    assert "Also spoken" in html and 'id="also"' in html
    assert "/api/models?kind=stt_languages" in js and "startBody" in js and "disabled" in js
    assert '"en"' not in js + api and '"de"' not in js + api and "'en'" not in js + api and "'de'" not in js + api


def test_js_uses_models_api_sse_poll_and_seek_without_innerhtml(mk_settings):
    js = "".join(fetch(mk_settings, "/analyse/analyse.js", "/analyse/api.js"))
    for needle in ("/api/models?kind=stt", "EventSource", "/events", "/api/jobs/", "setInterval", "currentTime",
                   "/api/recordings/", "/stt-runs", "unavailable_reason", "confirm", "textContent"):
        assert needle in js, needle
    assert "innerHTML" not in js
