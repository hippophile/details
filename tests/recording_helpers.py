"""Shared helpers for the recording tests: synthetic audio, fake ffmpeg, and an app with a test DB."""
import json, math, pathlib, stat, struct, wave

SR = 16000


def sine_pcm(seconds, freq=440.0, sr=SR):
    n = int(seconds * sr)
    return b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / sr))) for i in range(n))


def blocks(pcm, ms=100, sr=SR):
    step = sr * ms // 1000 * 2
    return [pcm[i:i + step] for i in range(0, len(pcm), step)]


def write_wav(path, pcm, sr=SR):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm)


def raw_wav_stale(path, pcm, sr=SR, tail=b""):
    """A WAV whose header says 0 data bytes (what a killed server leaves behind), plus optional torn tail."""
    hdr = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16) \
        + b"data" + struct.pack("<I", 0)
    pathlib.Path(path).write_bytes(hdr + pcm + tail)


def fake_tool(path, body):
    p = pathlib.Path(path)
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def read_json(p):
    return json.loads(pathlib.Path(p).read_text())
