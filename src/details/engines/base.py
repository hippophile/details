"""Engine contract. An engine returns segments with ABSOLUTE millisecond times (window offset already added)."""
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    text: str
    words: list | None = None
    avg_logprob: float | None = None
    no_speech_prob: float | None = None

    def to_dict(self):
        return asdict(self)


class SttEngine(Protocol):
    name: str
    supports_words: bool
    language: str | None      # language detected/used in the last window (the worker records it)
    lang_probs: dict | None   # {code: share} over primary + settings["also"] for the last window; None when not computed

    def __init__(self, model: dict): ...          # model = models.toml block (repo, ...)

    def load(self) -> None: ...

    def transcribe_window(self, audio_path: str, start_s: float, end_s: float, settings: dict) -> list[Segment]: ...
