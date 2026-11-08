"""speakers-diarization: the pyannote adapter's own logic without pyannote (real ffmpeg, fake annotation objects)."""
import types

import pytest

from details.engines import pyannote_diar as pd
from details.engines.diarization import DIAR_ENGINES, get_diar_engine
from library_helpers import FFMPEG, make_media, need_ffmpeg


def test_importing_the_adapter_needs_neither_torch_nor_pyannote():
    import subprocess, sys
    from details.settings import REPO_ROOT
    code = ("import sys\n"
            f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r})\n"
            "import details.engines.pyannote_diar as m\n"
            "print('torch' in sys.modules, 'pyannote.audio' in sys.modules)\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    assert r.stdout.split()[-2:] == ["False", "False"]


def test_registry_returns_the_adapter_lazily_and_unknown_names_raise():
    assert get_diar_engine("pyannote") is DIAR_ENGINES["pyannote"] is pd.PyannoteEngine
    with pytest.raises(KeyError, match="unknown diarization engine 'nope'"):
        get_diar_engine("nope")


def test_hook_arguments_become_progress_arguments():
    assert pd.hook_args("embeddings", 3, 10) == ("embeddings", 3, 10)
    assert pd.hook_args("embeddings", None, 10) == ("embeddings", 0, 10)
    assert pd.hook_args("segmentation", None, None) == ("segmentation", 1, 1)       # a finished step has no total


def test_turns_from_an_annotation_uses_ms_and_raw_labels():
    seg = lambda a, b: types.SimpleNamespace(start=a, end=b)
    ann = types.SimpleNamespace(itertracks=lambda yield_label: iter([(seg(0.0, 1.5), "t0", "SPEAKER_00"),
                                                                     (seg(1.5, 3.2504), "t1", "SPEAKER_01")]))
    assert pd.turns_from(ann) == [{"start_ms": 0, "end_ms": 1500, "speaker": "SPEAKER_00"},
                                  {"start_ms": 1500, "end_ms": 3250, "speaker": "SPEAKER_01"}]


def test_decode_pcm_gives_16k_mono_int16(tmp_path):
    need_ffmpeg()
    wav = make_media(tmp_path / "a.wav", "m4a", seconds=2, stereo=True)
    pcm = pd.decode_pcm(wav, FFMPEG)
    assert str(pcm.dtype) == "int16" and pcm.shape == (2 * pd.SR,) and abs(int(pcm.max())) > 1000


def test_decode_pcm_error_is_plain(tmp_path):
    need_ffmpeg()
    bad = tmp_path / "x.wav"
    bad.write_bytes(b"not audio")
    with pytest.raises(RuntimeError, match="ffmpeg could not decode the audio"):
        pd.decode_pcm(bad, FFMPEG)


def test_engine_passes_waveform_hints_and_hook_and_reads_the_exclusive_output(tmp_path):
    torch = pytest.importorskip("torch")
    need_ffmpeg()
    wav = make_media(tmp_path / "a.wav", "m4a", seconds=1)
    seen = {}
    seg = lambda a, b: types.SimpleNamespace(start=a, end=b)

    def pipeline(file, **kw):
        seen["file"], seen["kw"] = file, kw
        kw["hook"]("embeddings", None, file=file, total=4, completed=2)
        ann = types.SimpleNamespace(itertracks=lambda yield_label: iter([(seg(0, 1), None, "A")]))
        return types.SimpleNamespace(speaker_diarization="NOT THIS ONE", exclusive_speaker_diarization=ann)
    eng = pd.PyannoteEngine({"repo": "x/y", "ffmpeg": FFMPEG})
    eng.pipeline = pipeline
    got, progress = [], []
    turns = eng.diarize(str(wav), 2, 3, lambda *a: progress.append(a))
    assert turns == [{"start_ms": 0, "end_ms": 1000, "speaker": "A"}] and progress == [("embeddings", 2, 4)]
    f = seen["file"]
    assert f["sample_rate"] == 16000 and tuple(f["waveform"].shape) == (1, pd.SR) and f["waveform"].dtype == torch.float32
    assert seen["kw"]["min_speakers"] == 2 and seen["kw"]["max_speakers"] == 3
    eng.diarize(str(wav), None, None, lambda *a: None)
    assert "min_speakers" not in seen["kw"] and "max_speakers" not in seen["kw"]
