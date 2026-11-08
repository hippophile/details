"""Diarization worker child: `python -m details.engines.diar_worker <spec.json>`.
Spec: engine, model ({repo, ffmpeg}), audio_path, device, min_speakers, max_speakers, out_dir, hf_hub, plugins,
keepalive_s, stall_s, progress_min_interval_s. Writes <out_dir>/turns.json then worker.done.json (both atomic; NOT
done.json: that name is the runner's step marker), or error.json {message} on failure.
stdout is the heartbeat: `alive` every keepalive_s from process start (a model load can outlast the runner's heartbeat
timeout), `loaded <engine>` after the load, `progress <phase> <done>/<total>` from the engine's callback. The keepalive
stops when stall_s pass without a reset (load end, or any progress callback), so the runner's heartbeat timeout can
kill a hung worker. The Hugging Face token is never needed here: the model is read offline from the cache."""
import importlib, json, os, pathlib, re, sys, threading, time

from ..runner.joblog import redact
from ..settings import SECRET_ENV
from .diarization import get_diar_engine

_OUT = threading.Lock()


def emit(line):
    with _OUT:
        print(line, flush=True)


class Beat:
    """`alive` lines from process start until stall_s pass without reset()."""

    def __init__(self, keepalive_s, stall_s):
        self.keepalive_s, self.stall_s, self.last = keepalive_s, stall_s, time.monotonic()

    def reset(self):
        self.last = time.monotonic()

    def run(self):
        while True:
            if time.monotonic() - self.last <= self.stall_s:
                emit("alive")
            time.sleep(self.keepalive_s)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()


class Progress:
    """The engine callback: resets the stall clock on every call, prints at most one line per interval but always the
    first line of each phase."""

    def __init__(self, beat, interval):
        self.beat, self.interval, self.seen, self.last = beat, interval, set(), 0.0

    def __call__(self, phase, done, total):
        self.beat.reset()
        phase = re.sub(r"[^A-Za-z0-9_-]", "_", str(phase)) or "step"
        now = time.monotonic()
        if phase not in self.seen or self.interval <= 0 or now - self.last >= self.interval:
            self.seen.add(phase)
            self.last = now
            emit(f"progress {phase} {int(done)}/{int(total)}")


def write_atomic(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def set_environment(spec):
    """Offline, the configured cache, no token, pyannote usage metrics off, and the PyTorch MPS fallback only when MPS
    was asked for."""
    for name in SECRET_ENV:
        os.environ.pop(name, None)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_CACHE"] = str(spec["hf_hub"])
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"     # pyannote.audio 4.x sends usage metrics to otel.pyannote.ai unless off
    if spec.get("device") == "mps":
        os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"      # before anything imports torch


def main(argv):
    spec = json.loads(pathlib.Path(argv[0]).read_text(encoding="utf-8"))
    out = pathlib.Path(spec["out_dir"])
    set_environment(spec)
    beat = Beat(spec["keepalive_s"], spec["stall_s"])
    beat.start()                                      # the first "alive" is printed at once, from process start
    (out / "error.json").unlink(missing_ok=True)
    try:
        for mod in spec.get("plugins") or []:
            importlib.import_module(mod)
        eng = get_diar_engine(spec["engine"])(spec["model"])
        eng.load(spec["device"])
        beat.reset()
        emit(f"loaded {spec['engine']}")
        turns = eng.diarize(spec["audio_path"], spec.get("min_speakers"), spec.get("max_speakers"),
                            Progress(beat, spec["progress_min_interval_s"]))
        write_atomic(out / "turns.json", json.dumps({"turns": [
            {"start_ms": t["start_ms"], "end_ms": t["end_ms"], "speaker": t["speaker"]} for t in turns]}))
        write_atomic(out / "worker.done.json", json.dumps({"turns": len(turns)}))
        return 0
    except Exception as e:                       # a plain message for the UI; never a token (redact masks hf_... values)
        msg = redact((str(e) or type(e).__name__)[:500])
        write_atomic(out / "error.json", json.dumps({"message": msg}, ensure_ascii=False))
        print(f"error: {msg}", file=sys.stderr, flush=True)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
