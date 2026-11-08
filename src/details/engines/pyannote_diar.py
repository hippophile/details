"""pyannote.audio adapter (speaker-diarization-community-1). Runs inside the diar worker child process only: torch and
pyannote are imported inside load(), never at module level, so importing this module needs neither.
Audio is decoded by ffmpeg to 16 kHz mono and handed to the pipeline as an in-memory waveform (no torchcodec).
API facts checked against pyannote.audio 4.0.7 (2026-11-08, design.md "Verified assumptions"): the pipeline is called with
{"waveform": (1, T) float32 Tensor, "sample_rate": int}; keyword arguments min_speakers / max_speakers / hook; the result
is a DiarizeOutput with `exclusive_speaker_diarization`; the hook is called as hook(step_name, step_artifact, file=,
total=, completed=)."""
import pathlib, subprocess

from .diarization import DIAR_ENGINES

SR = 16000


def decode_pcm(audio_path, ffmpeg="ffmpeg"):
    """-> numpy int16 array, 16 kHz mono, decoded by ffmpeg on a pipe. Raises RuntimeError with ffmpeg's own message."""
    import numpy as np
    r = subprocess.run([ffmpeg or "ffmpeg", "-nostdin", "-v", "error", "-i", str(audio_path), "-f", "s16le", "-ac", "1",
                        "-ar", str(SR), "-"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode the audio: {r.stderr.decode(errors='replace').strip()[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.int16)


def turns_from(annotation):
    """pyannote Annotation -> [{start_ms, end_ms, speaker}] with the raw label (e.g. SPEAKER_00)."""
    return [{"start_ms": round(seg.start * 1000), "end_ms": round(seg.end * 1000), "speaker": str(label)}
            for seg, _, label in annotation.itertracks(yield_label=True)]


def hook_args(step_name, completed, total):
    """pyannote's hook -> the worker's progress(phase, done, total). A call without `total` reports a finished step."""
    if total is None:
        return str(step_name), 1, 1
    return str(step_name), int(completed or 0), int(total)


def snapshot_dir(repo):
    """The local snapshot folder of `repo` in the Hugging Face cache (HF_HUB_CACHE / HF_HUB_OFFLINE are set by the worker)."""
    from huggingface_hub import snapshot_download
    try:
        return pathlib.Path(snapshot_download(repo, local_files_only=True))
    except Exception:
        raise RuntimeError(f"the speaker model {repo} is not in the Hugging Face cache: run `make diar-model`") from None


class PyannoteEngine:
    name = "pyannote"

    def __init__(self, model):
        self.repo, self.ffmpeg, self.pipeline = model["repo"], model.get("ffmpeg") or "ffmpeg", None

    def load(self, device):
        import torch
        from pyannote.audio import Pipeline
        self.pipeline = Pipeline.from_pretrained(str(snapshot_dir(self.repo)))
        self.pipeline.to(torch.device(device))

    def diarize(self, audio_path, min_speakers, max_speakers, progress):
        import torch
        pcm = decode_pcm(audio_path, self.ffmpeg)
        waveform = torch.from_numpy(pcm.astype("float32") / 32768.0).unsqueeze(0)       # (1, samples)

        def hook(step_name, step_artifact, file=None, total=None, completed=None):
            progress(*hook_args(step_name, completed, total))
        kw = {k: v for k, v in (("min_speakers", min_speakers), ("max_speakers", max_speakers)) if v is not None}
        out = self.pipeline({"waveform": waveform, "sample_rate": SR}, hook=hook, **kw)
        # no overlapping turns: built for aligning with a transcript. A "legacy" pipeline returns the Annotation itself.
        return turns_from(getattr(out, "exclusive_speaker_diarization", out))


DIAR_ENGINES["pyannote"] = PyannoteEngine
