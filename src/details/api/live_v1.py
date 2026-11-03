"""Live Greek speech-to-text test page (v1), now served at /live.  Run:  make run   then open http://localhost:8765/live
Browser sends 16 kHz mono PCM over a WebSocket; server re-transcribes the current sentence ~every second (grey
"live" text), commits it when you pause, and on Stop runs one full pass over the whole take (best quality).
Each take is saved to sessions/<time>.wav + .txt so `make score` can score it against the script."""
import asyncio, concurrent.futures, datetime, importlib, json, pathlib, sys, time, wave
import numpy as np
from aiohttp import web

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "eval"))
from score import PROMPT  # same vocabulary hint as the scorer

SR = 16000
SESSIONS = REPO / "sessions"
JUNK = ("υπότιτλοι", "υποτιτλοι", "authorwave", "subtitles")   # known Whisper hallucinations on silence
GPU = concurrent.futures.ThreadPoolExecutor(max_workers=1)      # one model call at a time


def _mlxw():
    return importlib.import_module("mlx_whisper.transcribe")  # lazy: tests need no models


def models(hub):
    """Whisper MLX models already downloaded on this Mac."""
    out = []
    for d in sorted(hub.glob("models--mlx-community--whisper-*")):
        if any((d / "snapshots").glob("*/weights.*")):
            out.append("mlx-community/" + d.name.split("--", 2)[2])
    return out


def transcribe(audio, repo, lang, hint):
    t = time.time()
    r = _mlxw().transcribe(audio, path_or_hf_repo=repo, language=lang or None, condition_on_previous_text=False,
                        initial_prompt=PROMPT if hint else None, temperature=0.0 if len(audio) < SR * 30 else
                        (0.0, 0.2, 0.4), hallucination_silence_threshold=2.0 if len(audio) > SR * 30 else None)
    text = r["text"].strip()
    if any(j in text.lower() for j in JUNK):
        text = ""
    return text, time.time() - t


def db(x):
    return 20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-9)


class Session:
    def __init__(self, ws):
        self.ws, self.chunks, self.n, self.recording = ws, [], 0, False
        self.cfg = {"model": None, "lang": "el", "hint": False, "threshold": -45.0}

    def audio(self, a=0, b=None):
        if len(self.chunks) > 1:
            self.chunks = [np.concatenate(self.chunks)]
        return self.chunks[0][a:b] if self.chunks else np.zeros(0, np.float32)

    def speech_secs(self, x):
        f = 480  # 30 ms frames
        frames = x[: len(x) // f * f].reshape(-1, f) if len(x) >= f else np.zeros((0, f))
        rms_db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
        return float((rms_db > self.cfg["threshold"]).sum() * f / SR)

    async def run_model(self, x):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(GPU, transcribe, x, self.cfg["model"], self.cfg["lang"], self.cfg["hint"])

    async def send(self, **m):
        if not self.ws.closed:
            await self.ws.send_json(m)

    async def live_loop(self):
        seg, last_partial = 0, 0
        while self.recording:
            await asyncio.sleep(0.25)
            n = self.n
            x = self.audio(seg, n)
            if len(x) < SR * 0.5:
                continue
            tail_quiet = len(x) > SR * 0.8 and db(x[-int(SR * 0.5):]) < self.cfg["threshold"]
            too_long = len(x) > SR * 15
            has_speech = self.speech_secs(x) >= 0.3
            if (tail_quiet or too_long) and has_speech:
                cut = n
                if too_long and not tail_quiet:   # cut at the quietest 100 ms in the last 4 s
                    w = x[-SR * 4:]
                    k = int(np.argmin([db(w[i:i + 1600]) for i in range(0, len(w) - 1600, 400)]))
                    cut = n - SR * 4 + k * 400 + 800
                text, secs = await self.run_model(self.audio(seg, cut))
                await self.send(type="commit", text=text, secs=round(secs, 2), audio=round((cut - seg) / SR, 1))
                seg, last_partial = cut, cut
            elif tail_quiet and not has_speech:
                seg = max(seg, n - int(SR * 0.3))  # drop silence, keep a little lead-in
            elif has_speech and n - last_partial >= SR * 0.8:
                text, secs = await self.run_model(x)
                last_partial = n
                await self.send(type="partial", text=text, secs=round(secs, 2), audio=round(len(x) / SR, 1))

    async def stop(self):
        x = self.audio()
        if len(x) < SR * 0.5:
            return await self.send(type="final", text="", secs=0, audio=0, file="")
        await self.send(type="status", text="Full pass over the whole take…")
        text, secs = await self.run_model(x)
        SESSIONS.mkdir(exist_ok=True)
        stem = SESSIONS / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        with wave.open(str(stem) + ".wav", "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
        pathlib.Path(str(stem) + ".txt").write_text(text)
        await self.send(type="final", text=text, secs=round(secs, 1), audio=round(len(x) / SR, 1),
                        file=str(stem.relative_to(REPO)) + ".wav")


async def ws_handler(request):
    ws = web.WebSocketResponse(max_msg_size=0)
    await ws.prepare(request)
    s, task = Session(ws), None
    async for msg in ws:
        if msg.type == web.WSMsgType.BINARY and s.recording:
            s.chunks.append(np.frombuffer(msg.data, np.int16).astype(np.float32) / 32768)
            s.n += len(msg.data) // 2
        elif msg.type == web.WSMsgType.TEXT:
            m = json.loads(msg.data)
            s.cfg.update({k: m[k] for k in ("model", "lang", "hint", "threshold") if k in m})
            if m.get("type") == "start":
                await s.send(type="status", text=f"Loading {s.cfg['model'].split('/')[-1]}…")
                t = time.time()
                await asyncio.get_running_loop().run_in_executor(
                    GPU, _mlxw().ModelHolder.get_model, s.cfg["model"], importlib.import_module("mlx.core").float16)
                s.chunks, s.n, s.recording = [], 0, True
                await s.send(type="ready", text=f"Listening (model ready in {time.time() - t:.1f}s)")
                task = asyncio.create_task(s.live_loop())
            elif m.get("type") == "stop":
                s.recording = False
                if task:
                    await task
                await s.stop()
    s.recording = False
    return ws


def add_live_routes(app, settings):
    async def index(_):
        return web.FileResponse(REPO / "web" / "live-v1" / "index.html")

    async def list_models(_):
        return web.json_response(models(settings.paths.hf_hub))

    app.add_routes([web.get("/live", index), web.get("/models", list_models), web.get("/ws", ws_handler)])
