"""Fake STT engine for the worker child process. Registered as "fake" on import (and also as "mlx_whisper" when
FAKE_STT_OVERRIDE=1, so the API/step tests run the real pipeline without a GPU). Imported by the worker through
DETAILS_STT_PLUGINS=fakes.stt. Knobs (env, inherited by the child):
  FAKE_STT_CALLS=<file>      append one JSON line per transcribe_window call {start_s, end_s, settings}
  FAKE_STT_DELAY=<seconds>   sleep per window
  FAKE_STT_DIE_AT=<start_s>  os._exit(137) when asked to transcribe a window starting at or after this second
  FAKE_STT_PIDFILE=<file>    write the child pid at construction
  FAKE_STT_NAN=1             avg_logprob is NaN (mlx-whisper does this on a lone "!" in silence)
  FAKE_STT_ENVFILE=<file>    at construction: dump which of the 3 Hugging Face token variables are present (never values)"""
import json, os, time

from details.engines import ENGINES
from details.engines.base import Segment


class FakeSttEngine:
    name = "fake"
    supports_words = False

    def __init__(self, model):
        self.model, self.language = model, None
        if os.environ.get("FAKE_STT_PIDFILE"):
            open(os.environ["FAKE_STT_PIDFILE"], "w").write(str(os.getpid()))
        if os.environ.get("FAKE_STT_ENVFILE"):
            names = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "DETAILS_DIARIZATION_HF_TOKEN")
            open(os.environ["FAKE_STT_ENVFILE"], "w").write(json.dumps({n: n in os.environ for n in names}))

    def load(self):
        pass

    def transcribe_window(self, audio_path, start_s, end_s, settings):
        if os.environ.get("FAKE_STT_CALLS"):
            with open(os.environ["FAKE_STT_CALLS"], "a") as f:
                f.write(json.dumps({"start_s": start_s, "end_s": end_s, "settings": settings}) + "\n")
        if os.environ.get("FAKE_STT_DELAY"):
            time.sleep(float(os.environ["FAKE_STT_DELAY"]))
        die = os.environ.get("FAKE_STT_DIE_AT")
        if die is not None and start_s >= float(die):
            os._exit(137)
        self.language = settings.get("language") if settings.get("language") != "auto" else "el"
        lp = float("nan") if os.environ.get("FAKE_STT_NAN") == "1" else -0.2
        return [Segment(int(start_s * 1000), int(end_s * 1000), f"w{int(start_s)} Τι κάνεις?",
                        avg_logprob=lp, no_speech_prob=0.01)]


ENGINES["fake"] = FakeSttEngine
if os.environ.get("FAKE_STT_OVERRIDE") == "1":
    ENGINES["mlx_whisper"] = FakeSttEngine
