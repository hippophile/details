"""Live assist end to end in Brave (Playwright) with a fake microphone. Excluded from the default run (marker e2e).
Run:  .venv/bin/python -I -m pytest -m "e2e and not gpu" tests/e2e -s        (server on 127.0.0.1:8777, test DB)
Manual real-GPU check (marker gpu too):  ... -m "e2e and gpu" tests/e2e -s     (real Whisper turbo + Gemma E4B)
The app runs in-process in a thread with its own event loop; the browser is Brave from tests/brave-playwright.json.
`navigator.mediaDevices.getUserMedia` is overridden to play a wav (served by Playwright from a file) into a
MediaStreamDestination. Needs DATABASE_URL_TEST; never touches the app on the user's own port."""
import asyncio, json, os, pathlib, subprocess, sys, threading, time

import pytest

from conftest import make_settings, _test_url
from details.app import create_app
from details.settings import REPO_ROOT
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from live_helpers import script_pcm
from recording_helpers import SR, write_wav
from stt_helpers import SRC, TESTS, TURBO, hub_with

pytestmark = pytest.mark.e2e
PORT = int(os.environ.get("DETAILS_E2E_PORT", "8777"))
CONFIG = json.loads((REPO_ROOT / "tests" / "brave-playwright.json").read_text())["browser"]
FED = script_pcm(("t", 2.0), ("s", 1.0)) * 3                  # 9 s: three speech bursts with pauses
GOOD_NOTES = {"schema_version": 1, "notes": [{"text": "Συζήτηση", "quote": "Τι κάνεις"}], "tags": [],
              "action_items": [{"text": "Έλεγχος", "owner": None, "quote": "δεν υπάρχει τέτοια φράση"}]}

FAKE_MIC = """
(() => {
  window.__t0 = null; window.__ws = [];
  const NativeWS = window.WebSocket;
  window.WebSocket = function (...a) {
    const w = new NativeWS(...a);
    w.addEventListener("message", (e) => { if (typeof e.data === "string") window.__ws.push([performance.now(), e.data]); });
    return w;
  };
  window.WebSocket.prototype = NativeWS.prototype;
  Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  navigator.mediaDevices.getUserMedia = async () => {
    const ctx = new AudioContext({ sampleRate: 16000 });
    const buf = await ctx.decodeAudioData(await (await fetch("/e2e-audio.wav")).arrayBuffer());
    const src = ctx.createBufferSource(); src.buffer = buf;
    const dest = ctx.createMediaStreamDestination(); src.connect(dest);
    window.__t0 = performance.now(); src.start();
    return dest.stream;
  };
})();
"""


def skip(reason):
    sys.__stderr__.write(f"\nSKIP: {reason}\n")
    pytest.skip(reason)


def need_tools():
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        skip("playwright is not installed (pip install -r requirements-dev.txt)")
    exe = CONFIG["launchOptions"].get("executablePath")
    if not exe or not pathlib.Path(exe).exists():
        skip(f"Brave not found at {exe}")
    if not _test_url():
        skip("DATABASE_URL_TEST is not set: the end-to-end test needs the test database")


class Server:
    """The app on 127.0.0.1:PORT in a thread. `lm` = FakeLMStudio (HTTP + control transport) or None for the real LM Studio."""

    def __init__(self, tmp_path, db_url, lm=None, hub=True, **env):
        self.tmp, self.db_url, self.lm, self.env, self.hub = tmp_path, db_url, lm, env, hub
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.settings = None

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(60)

    def start(self):
        self.thread.start()
        self._run(self._start())
        return f"http://127.0.0.1:{PORT}"

    async def _start(self):
        from aiohttp import web
        env = {"DETAILS_STT_PLUGINS": os.environ.get("DETAILS_STT_PLUGINS", ""), **self.env}
        kw = {}
        if self.lm is not None:
            env["DETAILS_LMSTUDIO_URL"] = await self.lm.start_http()
            kw = {"lm_transport": self.lm, "meminfo": FakeMemInfo()}
        if self.hub:
            env["DETAILS_PATHS_HF_HUB"] = str(hub_with(self.tmp, TURBO))
        self.settings = make_settings(self.tmp / "data", self.db_url, **env)
        self.settings.paths.data_dir.mkdir(parents=True, exist_ok=True)
        self.app = create_app(self.settings, **kw)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", PORT).start()

    def stop(self):
        async def down():
            await self.runner.cleanup()
            if self.lm is not None:
                await self.lm.stop_http()
        try:
            self._run(down())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(10)


def fake_worker_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", f"{SRC}:{TESTS}")
    monkeypatch.setenv("FAKE_STT_OVERRIDE", "1")
    monkeypatch.setenv("DETAILS_STT_PLUGINS", "fakes.stt")        # read by Server() into the settings


def browse(wav, fn, *, color="light", size=(1100, 1500)):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        lo = CONFIG["launchOptions"]          # the JSON is the Node spelling
        b = p.chromium.launch(executable_path=lo["executablePath"], headless=lo.get("headless", True), args=lo.get("args"))
        try:
            ctx = b.new_context(permissions=CONFIG.get("contextOptions", {}).get("permissions"), color_scheme=color,
                                viewport={"width": size[0], "height": size[1]})
            ctx.add_init_script(FAKE_MIC)
            ctx.route("**/e2e-audio.wav", lambda r: r.fulfill(path=str(wav), content_type="audio/wav"))
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            if os.environ.get("DETAILS_E2E_DEBUG"):
                page.on("console", lambda m: print("console:", m.type, m.text))
            result = fn(page)
            assert errors == [], errors
            return result
        finally:
            b.close()


def flac_bursts(settings, rid):
    """Number of speech bursts in the saved master.flac (decoded with ffmpeg), to prove the audio is intact."""
    import numpy as np
    from details.services import recordings
    ff, _ = recordings.tools(settings)
    raw = subprocess.run([ff, "-v", "error", "-i", str(settings.paths.data_dir / "recordings" / rid / "master.flac"),
                          "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    win = SR // 10
    rms = [np.sqrt(np.mean(x[i:i + win] ** 2)) for i in range(0, len(x) - win, win)]
    loud = [r > 0.02 for r in rms]
    return sum(1 for i, v in enumerate(loud) if v and (i == 0 or not loud[i - 1]))


def finish_take(page, want_ms):
    page.wait_for_function(f"blocks >= {want_ms // 100}", timeout=60000)
    t = time.monotonic()
    page.click("#end")
    page.wait_for_selector("#saved:not([hidden])", timeout=20000)
    return time.monotonic() - t, page.evaluate("blocks")


def test_happy_path_live_text_notes_stop_saved(db, db_url, tmp_path, monkeypatch):
    need_tools()
    fake_worker_env(monkeypatch, tmp_path)
    wav = tmp_path / "fed.wav"
    write_wav(wav, FED)
    lm = FakeLMStudio()
    lm.responder = lambda body: GOOD_NOTES
    srv = Server(tmp_path, db_url, lm, DETAILS_LIVE_TEST_MODE="1", DETAILS_LIVE_TEST_INTERVAL_S="2")
    base = srv.start()
    shots = tmp_path / "shots"
    shots.mkdir()
    try:
        def drive(page):
            page.goto(base + "/record")
            assert not page.is_checked("#liveToggle") and page.is_hidden("#live")
            page.check("#liveToggle")
            assert page.is_visible("#live")
            page.click("#mic")
            page.wait_for_function("document.getElementById('liveToggle').disabled", timeout=10000)   # locked while recording
            page.wait_for_function("document.querySelectorAll('#liveLines .line').length >= 2", timeout=40000)
            page.wait_for_function("document.querySelectorAll('#liveNotes li').length >= 1", timeout=40000)
            notes_li = page.inner_text("#liveNotes li")
            actions_li = page.inner_text("#liveActions li")
            page.screenshot(path=str(shots / "live.png"))
            stop_s, blocks = finish_take(page, len(FED) // 2 * 1000 // SR)
            return notes_li, actions_li, stop_s, blocks, page.inner_text("#savedTitle"), page.inner_text("#liveStatus")
        notes_li, actions_li, stop_s, blocks, title, status = browse(wav, drive)
    finally:
        srv.stop()
    assert "unverified" not in notes_li and "unverified" in actions_li           # invented quote is marked, not dropped
    assert stop_s < 5
    (rid, dur) = db.execute("SELECT id, duration_ms FROM recording").fetchone()
    assert abs(dur - blocks * 100) <= 50                                         # saved = exactly what the browser sent
    assert dur >= 9000 - 100
    assert flac_bursts(srv.settings, str(rid)) == 3
    run = db.execute("SELECT status, settings, model_id FROM stt_run WHERE recording_id=%s", (rid,)).fetchall()
    assert len(run) == 1 and run[0][0] == "done" and run[0][1]["live_preview"] is True and run[0][2] == "whisper-turbo"
    assert db.execute("SELECT count(*) FROM transcript_segment").fetchone()[0] >= 2
    assert srv.app is not None and not [c for c in lm.calls if c[0] == "load" and "26b" in c[1]]
    print(f"\nE2E happy path: stop->saved {stop_s:.2f} s, recorded {dur} ms, browser blocks {blocks}")


def test_lm_studio_down_still_shows_text_and_saves(db, db_url, tmp_path, monkeypatch):
    need_tools()
    fake_worker_env(monkeypatch, tmp_path)
    wav = tmp_path / "fed.wav"
    write_wav(wav, FED)
    lm = FakeLMStudio(reachable=False)
    srv = Server(tmp_path, db_url, lm, DETAILS_LIVE_TEST_MODE="1", DETAILS_LIVE_TEST_INTERVAL_S="2")
    base = srv.start()
    try:
        def drive(page):
            page.goto(base + "/record")
            page.check("#liveToggle")
            page.click("#mic")
            page.wait_for_function("document.querySelectorAll('#liveLines .line').length >= 1", timeout=40000)
            page.wait_for_function("document.getElementById('liveStatus').innerText.includes('Cannot reach LM Studio')",
                                   timeout=40000)
            msg = page.inner_text("#liveStatus")
            timer_before = page.inner_text("#timer")
            finish_take(page, len(FED) // 2 * 1000 // SR)
            return msg, timer_before
        msg, timer = browse(wav, drive)
    finally:
        srv.stop()
    assert srv.settings.lmstudio.url in msg and "Recording and live text continue" in msg
    assert db.execute("SELECT count(*) FROM recording").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 1


# ---- record-pause: Start / Pause-Continue / End with a fake microphone (live assist off) ---------------------------
PAUSE_WAV = pathlib.Path(os.environ.get("DETAILS_E2E_GPU_WAV", "/Users/golem/code/play/llm-bench/asr-test/tts_melina.wav"))


def pause_take(db, db_url, tmp_path, drive):
    need_tools()
    if not PAUSE_WAV.exists():
        skip(f"{PAUSE_WAV} not found")
    srv = Server(tmp_path, db_url, None, hub=False)
    base = srv.start()
    try:
        out = browse(PAUSE_WAV, lambda page: drive(page, base))
    finally:
        srv.stop()
    rid, dur, source = db.execute("SELECT id, duration_ms, source FROM recording").fetchone()
    return out, dur, source


def test_pause_continue_end_saves_about_ten_seconds_and_one_pause(db, db_url, tmp_path):
    shots = pathlib.Path(os.environ.get("DETAILS_E2E_SHOTS", tmp_path / "shots"))
    shots.mkdir(parents=True, exist_ok=True)

    def drive(page, base):
        page.goto(base + "/record")
        assert page.is_disabled("#pause") and page.is_disabled("#end")
        page.click("#mic")
        page.wait_for_function("blocks >= 60", timeout=60000)               # 6 s
        page.click("#pause")
        page.wait_for_function("document.getElementById('pause').innerText === 'Continue'")
        timer, blocks_at_pause = page.inner_text("#timer"), page.evaluate("blocks")
        assert "Paused: nothing is recorded" in page.inner_text("#status") and page.is_enabled("#end")
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            page.screenshot(path=str(shots / f"paused-{scheme}.png"), full_page=True)
        time.sleep(3)                                                       # 3 s paused
        assert page.evaluate("blocks") == blocks_at_pause and page.inner_text("#timer") == timer
        page.click("#pause")
        page.wait_for_function("document.getElementById('pause').innerText === 'Pause'")
        page.wait_for_function(f"blocks >= {blocks_at_pause + 40}", timeout=60000)   # 4 s more
        _, blocks = finish_take(page, 0)
        return blocks, blocks_at_pause

    (blocks, at_pause), dur, source = pause_take(db, db_url, tmp_path, drive)
    print(f"\nE2E pause: saved {dur} ms, browser blocks {blocks}, pauses {json.dumps(source.get('pauses'))}")
    assert abs(dur - blocks * 100) <= 50 and abs(dur - 10000) <= 300
    (p,) = source["pauses"]
    assert abs(p["at_frames"] - at_pause * 1600) <= 1600 and "open" not in p and 2700 <= p["wall_paused_ms"] <= 3600
    assert "gaps" not in source


def test_pause_across_a_forced_ws_reconnect_keeps_one_pause(db, db_url, tmp_path):
    def drive(page, base):
        page.goto(base + "/record")
        page.click("#mic")
        page.wait_for_function("blocks >= 30", timeout=60000)
        page.click("#pause")
        page.evaluate("ws.close()")                                         # forced drop while paused
        page.wait_for_function("ready === false")
        page.wait_for_function("ready === true", timeout=20000)             # reconnected with `resume`
        page.wait_for_function("document.getElementById('status').innerText.includes('Paused')")
        assert page.inner_text("#pause") == "Continue"
        at_pause = page.evaluate("blocks")
        time.sleep(1)
        page.click("#pause")
        page.wait_for_function(f"blocks >= {at_pause + 20}", timeout=60000)
        _, blocks = finish_take(page, 0)
        return blocks

    blocks, dur, source = pause_take(db, db_url, tmp_path, drive)
    print(f"\nE2E pause+reconnect: saved {dur} ms, browser blocks {blocks}, pauses {json.dumps(source.get('pauses'))}")
    assert abs(dur - blocks * 100) <= 50
    assert len(source["pauses"]) == 1 and "open" not in source["pauses"][0]


# ---- manual real-GPU check (marker gpu + e2e): real Whisper turbo + Gemma E4B, ~3 minutes of real speech ------------
GPU_WAV = pathlib.Path(os.environ.get("DETAILS_E2E_GPU_WAV", "/Users/golem/code/play/llm-bench/asr-test/tts_melina.wav"))


@pytest.mark.gpu
def test_gpu_real_turbo_and_gemma_for_a_whole_script(db, db_url, tmp_path):
    """Prints the measurements (live text latency, RAM, rolling update time, stop->saved, FLAC vs browser). Needs LM Studio
    running with google/gemma-4-e4b downloaded (the user's own loaded model is never touched: only details-*)."""
    need_tools()
    import wave
    from details.runner.memory import VmStatMemInfo
    if not GPU_WAV.exists():
        skip(f"{GPU_WAV} not found")
    with wave.open(str(GPU_WAV)) as w:
        dur_s = w.getnframes() / w.getframerate()
    shots = pathlib.Path(os.environ.get("DETAILS_E2E_SHOTS", tmp_path / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    interval = os.environ.get("DETAILS_E2E_INTERVAL_S", "40")
    srv = Server(tmp_path, db_url, None, hub=False, DETAILS_LIVE_TEST_MODE="1", DETAILS_LIVE_TEST_INTERVAL_S=interval)
    base = srv.start()
    mem = VmStatMemInfo()
    ram = {"before": mem.free_gb(), "samples": []}

    def sample(label):
        ram["samples"].append((label, round(mem.free_gb(), 1)))

    def drive(page):
        page.goto(base + "/record")
        page.check("#liveToggle")
        page.click("#mic")
        page.wait_for_function("document.getElementById('liveStatus').innerText.includes('Speech: listening')", timeout=180000)
        sample("listening")
        shot_taken, t_end = False, time.monotonic() + dur_s + 60
        while time.monotonic() < t_end:
            time.sleep(5)
            sample("t=%ds" % page.evaluate("blocks / 10"))
            if not shot_taken and page.evaluate("document.querySelectorAll('#liveNotes li').length") > 0 \
                    and page.evaluate("blocks / 10") > dur_s * 0.5:
                for scheme in ("light", "dark"):
                    page.emulate_media(color_scheme=scheme)
                    page.screenshot(path=str(shots / f"live-{scheme}.png"), full_page=True)
                shot_taken = True
            if page.evaluate(f"blocks >= {int(dur_s * 10)}"):
                break
        stop_s, blocks = finish_take(page, int(dur_s * 1000))
        log = [(t, json.loads(m)) for t, m in page.evaluate("window.__ws")]
        return stop_s, blocks, log, page.evaluate("window.__t0"), page.inner_text("#liveStatus"), page.evaluate("document.querySelector('#saved .meta').innerText")

    try:
        stop_s, blocks, log, t0, status, meta = browse(GPU_WAV, drive, size=(1100, 1700))
        rid, dur = db.execute("SELECT id, duration_ms FROM recording").fetchone()
        lat = [t - t0 - m["end_ms"] for t, m in log if m.get("type") == "live_commit"]
        ups, pending = [], None
        for t, m in log:
            if m.get("type") == "live_status" and m.get("part") == "notes" and "Updating" in m["message"]:
                pending = t
            elif m.get("type") == "live_notes" and pending is not None:
                ups.append((round((t - pending) / 1000, 1), m["verified"], m["unverified"], len(m["notes"]), len(m["action_items"]), len(m["tags"])))
                pending = None
        errors = [m for _, m in log if m.get("type") == "live_status" and m.get("level") in ("error", "warn")]
        run = db.execute("SELECT id, settings, length(text) FROM stt_run WHERE recording_id=%s", (rid,)).fetchall()
        segs = db.execute("SELECT count(*) FROM transcript_segment").fetchone()[0]
        # Analyse must not auto-select the live preview when a full run exists: add one done non-live run, open the page
        full = db.execute("INSERT INTO stt_run (recording_id, engine, model_id, status, text, settings) "
                          "VALUES (%s,'mlx_whisper','whisper-large','done','x','{}') RETURNING id", (rid,)).fetchone()[0]
        db.execute("INSERT INTO transcript_segment (stt_run_id, idx, start_ms, end_ms, text) VALUES (%s,0,0,1000,'x')", (full,))

        def analyse(page):
            page.goto(f"{base}/analyse?rec={rid}")
            page.wait_for_selector("#runs li.sel", timeout=20000)
            page.screenshot(path=str(shots / "analyse.png"), full_page=True)
            return page.inner_text("#runs li.sel"), page.inner_text("#runs")
        selected, all_runs = browse(GPU_WAV, analyse)
    finally:
        srv.stop()
    lms = subprocess.run([str(pathlib.Path("~/.lmstudio/bin/lms").expanduser()), "ps"], capture_output=True, text=True).stdout
    print("\nGPU CHECK")
    print(f"  audio {dur_s:.1f} s | recorded {dur} ms | browser blocks {blocks} -> {blocks * 100} ms | stop->saved {stop_s:.2f} s")
    print(f"  commits {len(lat)} | latency after end of speech (estimate +-0.3 s): median {sorted(lat)[len(lat) // 2] / 1000:.1f} s, "
          f"max {max(lat) / 1000:.1f} s" if lat else "  NO COMMITS")
    print(f"  notes updates (wall s, verified, unverified, n_notes, n_actions, n_tags): {ups}")
    print(f"  warn/error statuses: {[(m['part'], m['level'], m['message'][:90]) for m in errors]}")
    print(f"  free RAM GB before {ram['before']:.1f}; samples {ram['samples']}")
    print(f"  stt_runs {[(r[1], r[2]) for r in run]} segments {segs}")
    print(f"  analyse selected: {selected!r}")
    print(f"  lms ps after: {lms.strip()}")
    print(f"  screenshots in {shots}")
    assert abs(dur - blocks * 100) <= 50 and lat and ups
    assert "(live preview)" not in selected and "(live preview)" in all_runs
    assert "details-" not in lms
