"""mlx-whisper adapter. Runs inside the worker child process only (imports mlx lazily in load())."""
from .base import Segment

SR = 16000


class MlxWhisperEngine:
    name = "mlx_whisper"
    supports_words = False

    def __init__(self, model):
        self.repo, self.language, self.audio = model["repo"], None, None
        self._mlxw = None

    def load(self):
        import importlib
        self._mlxw = importlib.import_module("mlx_whisper.transcribe")
        self._load_audio = importlib.import_module("mlx_whisper.audio").load_audio

    def transcribe_window(self, audio_path, start_s, end_s, settings):
        if self.audio is None:
            self.audio = self._load_audio(str(audio_path))
        x = self.audio[int(start_s * SR):int(end_s * SR)]
        lang = settings.get("language")
        r = self._mlxw.transcribe(x, path_or_hf_repo=self.repo, language=None if lang in (None, "auto") else lang,
                                  initial_prompt=settings.get("initial_prompt") or None,
                                  condition_on_previous_text=settings.get("condition_on_previous_text", False))
        self.language = r.get("language") or lang
        off = int(round(start_s * 1000))
        return [Segment(off + int(round(s["start"] * 1000)), off + int(round(s["end"] * 1000)), s["text"].strip(),
                        avg_logprob=s.get("avg_logprob"), no_speech_prob=s.get("no_speech_prob"))
                for s in r.get("segments", []) if s["text"].strip()]
