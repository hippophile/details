"""Live speech worker child: `python -m details.live.worker '<spec json>'`.
Spec: engine, model ({repo, ...}), language, initial_prompt, plugins. Loads the model, prints one JSON line
{"ready": true}, then serves requests until stdin closes:
  request  = a JSON line {"id", "n", "start_ms", "kind"} followed by n raw little-endian int16 bytes (16 kHz mono)
  reply    = one JSON line {"id", "text", "ms"} (or {"id", "error"}); replies are in request order.
The real mlx engine is called on the audio array directly; any other engine (tests, plugins) is file based, so the
chunk is written to a temp WAV. stdout carries the protocol only: library prints are sent to stderr."""
import importlib, json, os, sys, tempfile, time, wave

import numpy as np

from ..engines import get_engine

SR = 16000
JUNK = ("υπότιτλοι", "υποτιτλοι", "authorwave", "subtitles")      # known Whisper hallucinations on silence (as in v1)


def _direct_transcriber(repo, language, prompt):
    mlxw = importlib.import_module("mlx_whisper.transcribe")
    mlxw.ModelHolder.get_model(repo, importlib.import_module("mlx.core").float16)      # load now: ready means loaded

    def go(x, start_ms, kind):
        r = mlxw.transcribe(x, path_or_hf_repo=repo, language=language or None, condition_on_previous_text=False,
                            initial_prompt=prompt or None, temperature=0.0)
        return r["text"].strip()
    return go


def _file_transcriber(eng, language, prompt):
    eng.load()

    def go(x, start_ms, kind):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
                w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
            a = start_ms / 1000
            segs = eng.transcribe_window(path, a, a + len(x) / SR, {"language": language, "initial_prompt": prompt})
            return " ".join(s.text for s in segs).strip()
        finally:
            os.unlink(path)
    return go


def main(argv):
    spec = json.loads(argv[0])
    out = sys.stdout
    sys.stdout = sys.stderr
    for mod in spec.get("plugins") or []:
        importlib.import_module(mod)
    cls = get_engine(spec["engine"])
    lang, prompt = spec.get("language"), spec.get("initial_prompt")
    if cls.__name__ == "MlxWhisperEngine":
        go = _direct_transcriber(spec["model"]["repo"], lang, prompt)
    else:
        go = _file_transcriber(cls(spec["model"]), lang, prompt)

    def reply(m):
        out.write(json.dumps(m, ensure_ascii=False) + "\n")
        out.flush()
    reply({"ready": True})
    stdin = sys.stdin.buffer
    while True:
        line = stdin.readline()
        if not line:
            return 0
        req = json.loads(line)
        payload = stdin.read(req["n"])
        t0 = time.monotonic()
        try:
            x = np.frombuffer(payload, np.int16, len(payload) // 2).astype(np.float32) / 32768
            text = go(x, req.get("start_ms", 0), req.get("kind"))
            if any(j in text.lower() for j in JUNK):
                text = ""
            reply({"id": req["id"], "text": text, "ms": round((time.monotonic() - t0) * 1000)})
        except Exception as e:                       # one bad chunk must not end the session
            reply({"id": req["id"], "error": f"{type(e).__name__}: {e}"[:300]})


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
