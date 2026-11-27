"""mlx-whisper adapter. Runs inside the worker child process only (imports mlx lazily in load())."""
from .base import Segment

SR = 16000


def pick_language(probs, primary, also, min_share):
    """-> (language, shares). shares = Whisper's probabilities over primary + also, renormalised, 2 decimals.
    A non-primary language wins only with a share >= min_share (the best one); otherwise the window stays primary."""
    allowed = [primary, *also]
    raw = {c: probs.get(c, 0.0) for c in allowed}
    total = sum(raw.values())
    if total <= 0:
        return primary, {c: round(1 / len(allowed), 2) for c in allowed}
    share = {c: v / total for c, v in raw.items()}
    best = max(also, key=lambda c: share[c], default=None)
    lang = best if best is not None and share[best] >= min_share else primary
    return lang, {c: round(v, 2) for c, v in share.items()}


class MlxWhisperEngine:
    name = "mlx_whisper"
    supports_words = False

    def __init__(self, model):
        self.repo, self.language, self.lang_probs, self.audio = model["repo"], None, None, None
        self._mlxw = None

    def load(self):
        import importlib
        self._mlxw = importlib.import_module("mlx_whisper.transcribe")
        self._load_audio = importlib.import_module("mlx_whisper.audio").load_audio

    def _detect(self, x):
        """Whisper's probability per language code for the first 30 s of x (as in mlx_whisper.transcribe)."""
        import mlx.core as mx
        from mlx_whisper import audio as A
        model = self._mlxw.ModelHolder.get_model(self.repo, mx.float16)
        mel = A.log_mel_spectrogram(x, n_mels=model.dims.n_mels, padding=A.N_SAMPLES)
        _, probs = model.detect_language(A.pad_or_trim(mel, A.N_FRAMES, axis=-2).astype(mx.float16))
        return probs

    def transcribe_window(self, audio_path, start_s, end_s, settings):
        if self.audio is None:
            self.audio = self._load_audio(str(audio_path))
        x = self.audio[int(start_s * SR):int(end_s * SR)]
        lang, also = settings.get("language"), settings.get("also") or []
        self.lang_probs = None
        if also and lang not in (None, "auto"):
            lang, self.lang_probs = pick_language(self._detect(x), lang, also, settings.get("switch_min_share", 0.7))
        r = self._mlxw.transcribe(x, path_or_hf_repo=self.repo, language=None if lang in (None, "auto") else lang,
                                  initial_prompt=settings.get("initial_prompt") or None,
                                  condition_on_previous_text=settings.get("condition_on_previous_text", False))
        self.language = r.get("language") or lang
        off = int(round(start_s * 1000))
        return [Segment(off + int(round(s["start"] * 1000)), off + int(round(s["end"] * 1000)), s["text"].strip(),
                        avg_logprob=s.get("avg_logprob"), no_speech_prob=s.get("no_speech_prob"))
                for s in r.get("segments", []) if s["text"].strip()]
