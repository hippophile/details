"""Who talked end to end in Brave (Playwright) with the fake diarization engine. Excluded from the default run (marker e2e).
Run:  DETAILS_E2E_PORT=8791 .venv/bin/python -I -m pytest -m "e2e and not gpu" tests/e2e/test_diar_brave.py -s
The app runs in-process in a thread (see test_live_brave.Server) on 127.0.0.1:$DETAILS_E2E_PORT with the test DB; it never
touches the app on the user's own port. Screenshots: $DETAILS_E2E_SHOTS (default: a tmp folder)."""
import os, pathlib, subprocess

import pytest

from diar_helpers import PLUGIN, SEGMENTS, diar_hub, diar_ready, fake_diar_env, insert_diar_run, seed_transcript
from library_helpers import FFMPEG, seed
from stt_helpers import insert_run
from test_live_brave import Server, browse, need_tools, skip

pytestmark = pytest.mark.e2e
WAV = None


def audio_body(tmp_path, seconds=70):
    """A real 70 s FLAC (silence + a quiet tone): the player must be able to seek to 65 s."""
    out = tmp_path / "master.flac"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}:sample_rate=16000",
                    "-ac", "1", "-c:a", "flac", str(out)], check=True)
    return out.read_bytes()


def seeded(db, db_url, mk_settings, tmp_path, *, done_run=True, names=None):
    """-> (recording_id, stt_run_id, diarization_run_id|None): 70 s recording, done transcript, optional done 3-speaker run."""
    if not FFMPEG:
        skip("ffmpeg not installed")
    s = mk_settings(db_url)
    rid, run_id = seed_transcript(db, s)
    d = s.paths.data_dir / "recordings" / rid
    (d / "master.flac").write_bytes(audio_body(tmp_path))
    # speaker 1 talks again from 64.5 s, so the segment at 65 s is a turn row of its own (close segments of one speaker merge)
    turns = [("SPEAKER_01", 0, 20000), ("SPEAKER_00", 20000, 45000), ("SPEAKER_02", 45000, 64500), ("SPEAKER_01", 64500, 70000)]
    dr = insert_diar_run(db, rid, names=names, turns=turns) if done_run else None
    return rid, run_id, dr


def shots_dir(tmp_path):
    d = pathlib.Path(os.environ.get("DETAILS_E2E_SHOTS", tmp_path / "shots"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def server(tmp_path, db_url, monkeypatch, hub, *, importable=True, **env):
    if importable:
        diar_ready(monkeypatch)
    return Server(tmp_path, db_url, None, hub=False, DETAILS_PATHS_HF_HUB=str(hub), **PLUGIN, **env)


def test_bars_click_to_seek_chips_rename_and_reload(db, db_url, mk_settings, tmp_path, monkeypatch):
    need_tools()
    rid, run_id, dr = seeded(db, db_url, mk_settings, tmp_path)
    srv = server(tmp_path, db_url, monkeypatch, diar_hub(tmp_path))
    base = srv.start()
    shots = shots_dir(tmp_path)
    try:
        def drive(page):
            page.goto(f"{base}/analyse?rec={rid}")
            page.wait_for_selector("#spkBars .spk-row", timeout=20000)
            rows = page.locator("#spkBars .spk-row")
            assert rows.count() == 3
            first = rows.nth(0).inner_text()
            # name + percent + talk time are all text (colour is never the only cue)
            assert "Speaker 1" in first and "36%" in first and "00:25" in first
            assert page.is_visible("#spkWarn1") and page.is_visible("#spkWarn2") and page.is_visible("#spkGpuHint")
            assert "Speaker labels are a guess. Check before sharing." in page.inner_text("#spkWarn1")
            page.wait_for_selector("#transcript .seg .spk-chip", timeout=20000)
            chips = page.locator("#transcript .seg .spk-chip").all_inner_texts()
            assert chips == ["Speaker 1", "Speaker 1", "Speaker 2", "Speaker 2", "Speaker 3", "Speaker 3", "Speaker 1"]
            assert page.locator("#spkTimeline .spk-block").count() == 4
            page.screenshot(path=str(shots / "diar-card.png"), full_page=True)
            # click-to-seek: the last turn starts at 65 s (a turns row, then Enter on another one)
            turn = page.locator("#spkTurns .spk-turn", has_text="[01:05]")
            assert turn.count() == 1
            turn.click()
            page.wait_for_function("document.getElementById('player').currentTime >= 65 && !document.getElementById('player').paused",
                                   timeout=10000)
            assert 65 <= page.evaluate("document.getElementById('player').currentTime") < 68
            page.evaluate("document.getElementById('player').pause(); document.getElementById('player').currentTime = 0")
            page.locator("#spkTurns .spk-turn").nth(1).focus()
            page.keyboard.press("Enter")
            page.wait_for_function("document.getElementById('player').currentTime >= 20", timeout=10000)
            # rename speaker 1, then reload: bars, turns list and transcript chips all show it
            page.fill("#spkName1", "Ελένη")
            page.press("#spkName1", "Enter")
            page.wait_for_function("document.querySelector('#spkBars .spk-row .spk-name').innerText === 'Ελένη'", timeout=10000)
            page.reload()
            page.wait_for_selector("#spkBars .spk-row", timeout=20000)
            page.wait_for_selector("#transcript .seg .spk-chip", timeout=20000)
            return (page.locator("#spkBars .spk-name").all_inner_texts(), page.locator("#spkTurns .spk-chip").all_inner_texts(),
                    page.locator("#transcript .seg .spk-chip").all_inner_texts(), page.input_value("#spkName1"),
                    page.locator("#aiLabels option").all_inner_texts())
        names, turn_chips, seg_chips, field, labels = browse(None_wav(tmp_path), drive)
    finally:
        srv.stop()
    assert names[0] == "Ελένη" and names[1:] == ["Speaker 2", "Speaker 3"]
    assert "Ελένη" in turn_chips and "Ελένη" in seg_chips and field == "Ελένη"
    assert len(labels) == 2 and labels[0].startswith("No labels")


def None_wav(tmp_path):
    p = tmp_path / "unused.wav"
    p.write_bytes(b"RIFF")
    return p


def test_disabled_state_shows_reason_and_hint_and_no_bars(db, db_url, mk_settings, tmp_path, monkeypatch):
    need_tools()
    rid, run_id, _ = seeded(db, db_url, mk_settings, tmp_path, done_run=False)
    srv = server(tmp_path, db_url, monkeypatch, diar_hub(tmp_path, "missing"))
    base = srv.start()
    shots = shots_dir(tmp_path)
    try:
        def drive(page):
            page.goto(f"{base}/analyse?rec={rid}")
            page.wait_for_function("document.getElementById('spkReason').innerText.length > 0", timeout=20000)
            page.screenshot(path=str(shots / "diar-disabled.png"), full_page=True)
            return (page.is_disabled("#spkStart"), page.inner_text("#spkReason"), page.locator("#spkBars .spk-row").count(),
                    page.inner_text("#spkEmpty"), page.is_visible("#spkCard"))
        disabled, reason, bars, empty, shown = browse(None_wav(tmp_path), drive)
    finally:
        srv.stop()
    assert disabled and shown and bars == 0
    assert "not downloaded" in reason and "HF_TOKEN" in reason and "make diar-model" in reason
    assert empty == "The speaker model is unavailable."


def test_start_a_run_reload_mid_run_shows_the_running_card_then_finishes(db, db_url, mk_settings, tmp_path, monkeypatch):
    need_tools()
    rid, run_id, _ = seeded(db, db_url, mk_settings, tmp_path, done_run=False)
    fake_diar_env(monkeypatch, tmp_path, delay=4, callbacks=3)
    srv = server(tmp_path, db_url, monkeypatch, diar_hub(tmp_path), DETAILS_DIARIZATION_PROGRESS_MIN_INTERVAL_S="0")
    base = srv.start()
    shots = shots_dir(tmp_path)
    try:
        def drive(page):
            page.goto(f"{base}/analyse?rec={rid}")
            page.wait_for_function("!document.getElementById('spkStart').disabled", timeout=20000)
            page.fill("#spkMin", "2")
            page.fill("#spkMax", "3")
            page.click("#spkStart")
            page.wait_for_selector("#progress:not([hidden])", timeout=20000)
            page.reload()                                                   # the page forgot everything; the run list knows the job
            page.wait_for_selector("#progress:not([hidden])", timeout=20000)
            page.wait_for_function("document.getElementById('stage').innerText.includes('Speakers')", timeout=30000)
            stage = page.inner_text("#stage")
            page.screenshot(path=str(shots / "diar-running.png"), full_page=True)
            page.wait_for_selector("#spkBars .spk-row", timeout=60000)
            return stage, page.locator("#spkBars .spk-row").count(), page.inner_text("#spkRun option:checked")
        stage, bars, label = browse(None_wav(tmp_path), drive)
    finally:
        srv.stop()
    assert "Speakers" in stage and bars == 3 and "3 speakers" in label
    row = db.execute("SELECT status, settings FROM diarization_run").fetchone()
    assert row[0] == "done" and row[1]["min_speakers"] == 2 and row[1]["max_speakers"] == 3


def test_dark_mode_keeps_names_and_colours_readable(db, db_url, mk_settings, tmp_path, monkeypatch):
    need_tools()
    rid, run_id, dr = seeded(db, db_url, mk_settings, tmp_path, names={2: "Νίκος"})
    srv = server(tmp_path, db_url, monkeypatch, diar_hub(tmp_path))
    base = srv.start()
    shots = shots_dir(tmp_path)
    try:
        def drive(page):
            page.goto(f"{base}/analyse?rec={rid}")
            page.wait_for_selector("#spkBars .spk-row", timeout=20000)
            page.wait_for_selector("#transcript .seg .spk-chip", timeout=20000)
            page.locator("#spkCard").screenshot(path=str(shots / "diar-card-dark.png"))
            # the chip colour is not the page text colour in dark mode: it is the lighter dark-mode palette, still a colour
            return page.evaluate("getComputedStyle(document.querySelector('#spkBars .spk-row')).getPropertyValue('--c').trim()"), \
                page.locator("#spkBars .spk-name").all_inner_texts()
        colour, names = browse(None_wav(tmp_path), drive, color="dark")
    finally:
        srv.stop()
    assert colour == "#7aa8ff" and names == ["Speaker 1", "Νίκος", "Speaker 3"]
