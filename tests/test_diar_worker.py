"""speakers-diarization: the diarization worker child process with the fake engine."""
import json, subprocess, sys, time

import pytest

from diar_helpers import SENTINEL, TOKEN_NAMES, fake_diar_env, lines


def spec(tmp_path, hub=None, **over):
    out = tmp_path / "step"
    out.mkdir(exist_ok=True)
    p = tmp_path / "spec.json"
    body = {"engine": "fake", "model": {"repo": "x/y", "ffmpeg": ""}, "audio_path": str(tmp_path / "a.flac"),
            "device": "cpu", "min_speakers": None, "max_speakers": None, "out_dir": str(out),
            "hf_hub": str(hub or tmp_path / "hub"), "plugins": ["fakes.diar"], "keepalive_s": 15, "stall_s": 600,
            "progress_min_interval_s": 0, **over}
    p.write_text(json.dumps(body))
    return p, out


def run_worker(p, env=None):
    import os
    e = None if env is None else {**os.environ, **env}
    return subprocess.run([sys.executable, "-m", "details.engines.diar_worker", str(p)], capture_output=True, text=True,
                          timeout=30, env=e)


def test_spec_in_turns_and_done_files_out(tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path)
    p, out = spec(tmp_path)
    r = run_worker(p)
    assert r.returncode == 0, r.stderr[-400:]
    turns = json.loads((out / "turns.json").read_text())["turns"]
    assert [t["speaker"] for t in turns] == ["SPEAKER_01", "SPEAKER_00", "SPEAKER_02"] and turns[0]["end_ms"] == 20000
    assert (out / "worker.done.json").exists()
    assert not (out / "done.json").exists()           # that name belongs to the runner's step marker
    assert not list(out.glob("*.tmp"))                # written atomically


def test_heartbeat_loaded_line_and_one_progress_line_per_callback(tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, callbacks=3)
    p, _ = spec(tmp_path, progress_min_interval_s=0)
    out_lines = [x for x in run_worker(p).stdout.splitlines() if x.strip()]
    assert any(x.startswith("loaded") for x in out_lines)
    assert [x for x in out_lines if x.startswith("progress ")] == [
        "progress embeddings 1/3", "progress embeddings 2/3", "progress embeddings 3/3"]


def test_throttled_progress_still_prints_at_least_one_line(tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, callbacks=50)
    p, _ = spec(tmp_path, progress_min_interval_s=1.0)
    prog = [x for x in run_worker(p).stdout.splitlines() if x.startswith("progress ")]
    assert 1 <= len(prog) <= 2 and prog[0] == "progress embeddings 1/50"


def test_each_phase_prints_at_least_one_line_even_when_throttled(tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, callbacks=2)
    p, _ = spec(tmp_path, progress_min_interval_s=1000)
    prog = [x for x in run_worker(p).stdout.splitlines() if x.startswith("progress ")]
    assert prog == ["progress embeddings 1/2"]


def test_environment_is_offline_with_the_configured_cache_and_no_token_variables(tmp_path, monkeypatch):
    paths = fake_diar_env(monkeypatch, tmp_path)
    for n in TOKEN_NAMES:
        monkeypatch.setenv(n, SENTINEL)
    p, _ = spec(tmp_path, hub=tmp_path / "myhub")
    r = run_worker(p)
    assert r.returncode == 0, r.stderr[-300:]
    got = json.loads(paths.envfile.read_text())
    assert got["HF_HUB_OFFLINE"] == "1" and got["HF_HUB_CACHE"] == str(tmp_path / "myhub")
    assert got["tokens"] == {n: False for n in TOKEN_NAMES}
    assert got["PYTORCH_ENABLE_MPS_FALLBACK"] is None
    assert got["PYANNOTE_METRICS_ENABLED"] == "0"          # pyannote 4.x telemetry (otel.pyannote.ai) is off: nothing leaves the Mac
    assert got["real_adapter_imported"] is False          # the fake replaced the real "pyannote" adapter
    assert SENTINEL not in r.stdout + r.stderr + p.read_text()


def test_device_and_speaker_hints_reach_the_engine_and_mps_enables_the_fallback(tmp_path, monkeypatch):
    paths = fake_diar_env(monkeypatch, tmp_path)
    p, _ = spec(tmp_path, device="mps", min_speakers=2, max_speakers=3)
    assert run_worker(p).returncode == 0
    assert lines(paths.calls) == [{"device": "mps", "min_speakers": 2, "max_speakers": 3}]
    assert json.loads(paths.envfile.read_text())["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"


def test_no_hints_means_none_and_cpu_has_no_fallback_variable(tmp_path, monkeypatch):
    paths = fake_diar_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    p, _ = spec(tmp_path)
    assert run_worker(p).returncode == 0
    assert lines(paths.calls) == [{"device": "cpu", "min_speakers": None, "max_speakers": None}]


def test_failure_writes_error_json_and_no_turns(tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, fail="the model exploded")
    p, out = spec(tmp_path)
    r = run_worker(p)
    assert r.returncode != 0
    assert "the model exploded" in json.loads((out / "error.json").read_text())["message"]
    assert not (out / "turns.json").exists() and not (out / "worker.done.json").exists()


def test_load_failure_writes_error_json(tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, load_fail="cannot load weights")
    p, out = spec(tmp_path)
    assert run_worker(p).returncode != 0
    assert "cannot load weights" in json.loads((out / "error.json").read_text())["message"]


def test_error_message_is_redacted(tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, fail=f"401 for {SENTINEL}")
    p, out = spec(tmp_path)
    r = run_worker(p)
    assert r.returncode != 0
    assert SENTINEL not in (out / "error.json").read_text() + r.stdout


def test_unknown_engine_is_a_plain_error(tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path)
    p, out = spec(tmp_path, engine="nope")
    assert run_worker(p).returncode != 0
    assert "nope" in json.loads((out / "error.json").read_text())["message"]


def test_keepalive_lines_during_a_slow_model_load(tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, load_delay=1.5)
    p, _ = spec(tmp_path, keepalive_s=0.3)
    out_lines = run_worker(p).stdout.splitlines()
    first_loaded = next(i for i, x in enumerate(out_lines) if x.startswith("loaded"))
    assert out_lines[:first_loaded].count("alive") >= 3          # beats from process start, before the load ended


def test_keepalive_stops_after_stall_s_while_the_process_stays_alive(tmp_path, monkeypatch):
    fake_diar_env(monkeypatch, tmp_path, hang=1)
    p, _ = spec(tmp_path, keepalive_s=0.2, stall_s=0.8)
    proc = subprocess.Popen([sys.executable, "-m", "details.engines.diar_worker", str(p)], stdout=subprocess.PIPE,
                            text=True)
    stamps = []
    import threading

    def reader():
        for line in proc.stdout:
            stamps.append((time.monotonic(), line.strip()))
    t0 = time.monotonic()
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    time.sleep(4.0)
    try:
        assert proc.poll() is None                                   # still hanging: the runner would have to kill it
        beats = [s for s, x in stamps if x == "alive"]
        assert beats, "no keepalive at all"
        assert max(beats) - t0 < 2.5                                 # nothing after stall_s (plus start-up time)
        assert not [s for s, x in stamps if s - t0 > 3.0]
    finally:
        proc.kill()
        proc.wait()
