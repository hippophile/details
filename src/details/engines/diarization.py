"""Name-keyed diarization engine registry (kept apart from engines/__init__.py: the STT worker never imports it).
An engine is a class with `__init__(model: dict)`, `load(device)` and
`diarize(audio_path, min_speakers, max_speakers, progress) -> [{start_ms, end_ms, speaker}]`, where `progress(phase,
done, total)` is called as the work advances. Adding an engine = one class + one line here + one models.toml block."""
import importlib

DIAR_ENGINES: dict = {}


def get_diar_engine(name):
    if name not in DIAR_ENGINES and name == "pyannote":
        importlib.import_module(".pyannote_diar", __package__)    # lazy: torch is only imported inside load()
    if name not in DIAR_ENGINES:
        raise KeyError(f"unknown diarization engine '{name}'")
    return DIAR_ENGINES[name]
