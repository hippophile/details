"""Name-keyed engine registry. Adding an engine = one class + one line here + one models.toml block."""
import importlib

ENGINES: dict = {}


def get_engine(name):
    if name not in ENGINES and name == "mlx_whisper":
        importlib.import_module(".mlx_whisper", __name__)     # lazy: no model code is imported until needed
    if name not in ENGINES:
        raise KeyError(f"unknown STT engine '{name}'")
    return ENGINES[name]


def _register_builtin():
    from .mlx_whisper import MlxWhisperEngine
    ENGINES["mlx_whisper"] = MlxWhisperEngine


_register_builtin()
