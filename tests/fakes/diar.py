"""Fake diarization engine for the diar worker child process. Registered as "fake" on import (and as "pyannote" when
FAKE_DIAR_OVERRIDE=1, so the API/step tests run the real pipeline without torch). Imported by the worker through
DETAILS_DIARIZATION_PLUGINS=fakes.diar. Knobs (env, inherited by the child):
  FAKE_DIAR_TURNS=<json>      list of {start_ms, end_ms, speaker} the engine returns (default: 3 speakers over 66 s)
  FAKE_DIAR_CALLS=<file>      append one JSON line per diarize call {device, min_speakers, max_speakers}
  FAKE_DIAR_ENVFILE=<file>    at load: dump HF_HUB_OFFLINE, HF_HUB_CACHE, PYTORCH_ENABLE_MPS_FALLBACK, PYANNOTE_METRICS_ENABLED and, per token
                              variable name, whether it is present (never its value)
  FAKE_DIAR_PIDFILE=<file>    write the child pid at construction
  FAKE_DIAR_DELAY=<seconds>   sleep inside diarize (after the callbacks)
  FAKE_DIAR_LOAD_DELAY=<s>    sleep inside load (a slow model load)
  FAKE_DIAR_HANG=1            never return from diarize (after load)
  FAKE_DIAR_FAIL=<message>    raise RuntimeError(message) inside diarize
  FAKE_DIAR_LOAD_FAIL=<msg>   raise RuntimeError(message) inside load
  FAKE_DIAR_STDERR_TOKEN=<s>  write s to stderr only, then os._exit(1)
  FAKE_DIAR_CALLBACKS=<n>     number of progress callbacks (phase "embeddings", i/n)"""
import json, os, sys, time

from details.engines.diarization import DIAR_ENGINES

TOKEN_NAMES = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "DETAILS_DIARIZATION_HF_TOKEN")
DEFAULT_TURNS = [{"start_ms": 0, "end_ms": 20000, "speaker": "SPEAKER_01"},
                 {"start_ms": 20000, "end_ms": 45000, "speaker": "SPEAKER_00"},
                 {"start_ms": 45000, "end_ms": 66000, "speaker": "SPEAKER_02"}]


class FakeDiarEngine:
    name = "fake"

    def __init__(self, model):
        self.model, self.device = model, None
        if os.environ.get("FAKE_DIAR_PIDFILE"):
            open(os.environ["FAKE_DIAR_PIDFILE"], "w").write(str(os.getpid()))

    def load(self, device):
        self.device = device
        if os.environ.get("FAKE_DIAR_ENVFILE"):
            rec = {"HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE"), "HF_HUB_CACHE": os.environ.get("HF_HUB_CACHE"),
                   "PYTORCH_ENABLE_MPS_FALLBACK": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
                   "PYANNOTE_METRICS_ENABLED": os.environ.get("PYANNOTE_METRICS_ENABLED"),
                   "tokens": {n: n in os.environ for n in TOKEN_NAMES},
                   "real_adapter_imported": "details.engines.pyannote_diar" in sys.modules}
            open(os.environ["FAKE_DIAR_ENVFILE"], "w").write(json.dumps(rec))
        if os.environ.get("FAKE_DIAR_LOAD_DELAY"):
            time.sleep(float(os.environ["FAKE_DIAR_LOAD_DELAY"]))
        if os.environ.get("FAKE_DIAR_LOAD_FAIL"):
            raise RuntimeError(os.environ["FAKE_DIAR_LOAD_FAIL"])

    def diarize(self, audio_path, min_speakers, max_speakers, progress):
        if os.environ.get("FAKE_DIAR_CALLS"):
            with open(os.environ["FAKE_DIAR_CALLS"], "a") as f:
                f.write(json.dumps({"device": self.device, "min_speakers": min_speakers,
                                    "max_speakers": max_speakers}) + "\n")
        n = int(os.environ.get("FAKE_DIAR_CALLBACKS", "2"))
        for i in range(n):
            progress("embeddings", i + 1, n)
        if os.environ.get("FAKE_DIAR_DELAY"):
            time.sleep(float(os.environ["FAKE_DIAR_DELAY"]))
        if os.environ.get("FAKE_DIAR_HANG") == "1":
            time.sleep(3600)
        if os.environ.get("FAKE_DIAR_STDERR_TOKEN"):
            sys.stderr.write("401 Unauthorized for token " + os.environ["FAKE_DIAR_STDERR_TOKEN"] + "\n")
            sys.stderr.flush()
            os._exit(1)
        if os.environ.get("FAKE_DIAR_FAIL"):
            raise RuntimeError(os.environ["FAKE_DIAR_FAIL"])
        return json.loads(os.environ["FAKE_DIAR_TURNS"]) if os.environ.get("FAKE_DIAR_TURNS") else DEFAULT_TURNS


DIAR_ENGINES["fake"] = FakeDiarEngine
if os.environ.get("FAKE_DIAR_OVERRIDE") == "1":
    DIAR_ENGINES["pyannote"] = FakeDiarEngine
