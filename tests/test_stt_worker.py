"""stt-runs: the worker child process with the fake engine (checkpoint, resume, heartbeat, settings)."""
import json, subprocess, sys

from stt_helpers import fake_worker_env

SETTINGS = {"language": "el", "initial_prompt": "hint", "condition_on_previous_text": False}


def spec(tmp_path, seconds=5, window_s=1, **over):
    out = tmp_path / "step"
    out.mkdir(exist_ok=True)
    p = tmp_path / "spec.json"
    p.write_text(json.dumps({"engine": "fake", "model": {"repo": "x/y"}, "audio_path": str(tmp_path / "a.flac"),
                             "duration_s": seconds, "window_s": window_s, "settings": SETTINGS,
                             "out_dir": str(out), "plugins": ["fakes.stt"], **over}))
    return p, out


def run_worker(p):
    r = subprocess.run([sys.executable, "-m", "details.engines.worker", str(p)], capture_output=True, text=True,
                       timeout=30)
    return r


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def test_writes_one_jsonl_line_per_window_in_out_dir(tmp_path, monkeypatch):
    fake_worker_env(monkeypatch, tmp_path)
    p, out = spec(tmp_path)
    r = run_worker(p)
    assert r.returncode == 0, r.stderr[-400:]
    got = lines(out / "segments.jsonl")
    assert [g["idx"] for g in got] == [0, 1, 2, 3, 4]
    assert got[2]["segments"][0]["text"] == "w2 Τι κάνεις?" and got[2]["segments"][0]["start_ms"] == 2000
    assert (out / "worker.done.json").exists()
    assert not (out / "done.json").exists()      # that name belongs to the runner's step marker


def test_heartbeat_line_at_load_and_one_per_window(tmp_path, monkeypatch):
    fake_worker_env(monkeypatch, tmp_path)
    p, _ = spec(tmp_path, seconds=3)
    out_lines = [x for x in run_worker(p).stdout.splitlines() if x.strip()]
    assert "loaded" in out_lines[0]
    assert len(out_lines) >= 1 + 3
    assert [x for x in out_lines[1:] if x.startswith("window ")][:3] == ["window 1/3", "window 2/3", "window 3/3"]


def test_settings_reach_the_engine(tmp_path, monkeypatch):
    calls = fake_worker_env(monkeypatch, tmp_path)
    p, _ = spec(tmp_path, seconds=1)
    assert run_worker(p).returncode == 0
    s = json.loads(calls.read_text().splitlines()[0])["settings"]
    assert s["language"] == "el" and s["initial_prompt"] == "hint" and s["condition_on_previous_text"] is False


def test_killed_after_three_windows_resumes_with_the_last_two(tmp_path, monkeypatch):
    calls = fake_worker_env(monkeypatch, tmp_path, die_at=3)
    p, out = spec(tmp_path)
    r = run_worker(p)
    assert r.returncode != 0
    assert [g["idx"] for g in lines(out / "segments.jsonl")] == [0, 1, 2]
    calls.unlink()
    monkeypatch.delenv("FAKE_STT_DIE_AT")
    r = run_worker(p)
    assert r.returncode == 0, r.stderr[-400:]
    assert [json.loads(x)["start_s"] for x in calls.read_text().splitlines()] == [3, 4]   # only windows 4 and 5
    assert [g["idx"] for g in lines(out / "segments.jsonl")] == [0, 1, 2, 3, 4]


def test_torn_last_line_is_ignored_and_redone(tmp_path, monkeypatch):
    calls = fake_worker_env(monkeypatch, tmp_path)
    p, out = spec(tmp_path, seconds=3)
    assert run_worker(p).returncode == 0
    f = out / "segments.jsonl"
    f.write_text("".join(f.read_text().splitlines(True)[:2]) + '{"idx": 2, "segm')   # killed mid-write
    calls.unlink()
    assert run_worker(p).returncode == 0
    assert [json.loads(x)["start_s"] for x in calls.read_text().splitlines()] == [2]
    assert [g["idx"] for g in lines(f)] == [0, 1, 2]


def test_last_window_is_clipped_to_the_duration(tmp_path, monkeypatch):
    calls = fake_worker_env(monkeypatch, tmp_path)
    p, _ = spec(tmp_path, seconds=2.5, window_s=1)
    assert run_worker(p).returncode == 0
    c = [json.loads(x) for x in calls.read_text().splitlines()]
    assert [(x["start_s"], x["end_s"]) for x in c] == [(0, 1), (1, 2), (2, 2.5)]


def test_unknown_engine_exits_nonzero_with_a_message(tmp_path, monkeypatch):
    fake_worker_env(monkeypatch, tmp_path)
    p, _ = spec(tmp_path, engine="nope")
    r = run_worker(p)
    assert r.returncode != 0 and "nope" in r.stderr
