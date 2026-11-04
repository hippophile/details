"""Helpers for the stt-runs tests: run rows, env for the fake worker child, a fake Hugging Face hub."""
import pathlib, uuid

from details.settings import REPO_ROOT
from library_helpers import seed

SRC, TESTS = REPO_ROOT / "src", pathlib.Path(__file__).resolve().parent
TURBO = "models--mlx-community--whisper-large-v3-turbo"
LARGE = "models--mlx-community--whisper-large-v3-mlx"


def fake_worker_env(monkeypatch, tmp_path, override=True, **knobs):
    """The worker child inherits this env: it finds `details` + the fakes and loads the fake engine plugin."""
    monkeypatch.setenv("PYTHONPATH", f"{SRC}:{TESTS}")
    if override:
        monkeypatch.setenv("FAKE_STT_OVERRIDE", "1")
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_STT_CALLS", str(calls))
    for k, v in knobs.items():
        monkeypatch.setenv(f"FAKE_STT_{k.upper()}", str(v))
    return calls


def hub_with(tmp_path, *dirs):
    hub = tmp_path / "hub"
    for d in dirs:
        w = hub / d / "snapshots" / "abc"
        w.mkdir(parents=True)
        (w / "weights.safetensors").write_bytes(b"x")
    return hub


def rec(db, settings, seconds=5, title="rec"):
    rid = seed(db, settings, title)
    db.execute("UPDATE recording SET duration_ms=%s WHERE id=%s", (int(seconds * 1000), rid))
    return rid


def insert_run(db, rid, status="done", model_id="whisper-large", engine="mlx_whisper", **cols):
    cols = {"recording_id": rid, "engine": engine, "model_id": model_id, "status": status, **cols}
    names = ", ".join(cols)
    ph = ", ".join(["%s"] * len(cols))
    return str(db.execute(f"INSERT INTO stt_run ({names}) VALUES ({ph}) RETURNING id", list(cols.values())).fetchone()[0])


def insert_segments(db, run_id, n):
    for i in range(n):
        db.execute("INSERT INTO transcript_segment (stt_run_id, idx, start_ms, end_ms, text) VALUES (%s,%s,%s,%s,%s)",
                   (run_id, i, i * 1000, i * 1000 + 900, f"s{i}"))


def run(coro_fn, timeout=40):
    """Like jobs_helpers.run but returns the coroutine's result."""
    import asyncio
    return asyncio.run(asyncio.wait_for(coro_fn(), timeout))
