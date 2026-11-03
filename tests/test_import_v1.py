import datetime, hashlib, zoneinfo

from details import import_v1
from recording_helpers import sine_pcm, write_wav


def setup(tmp_path):
    src = tmp_path / "sessions"
    src.mkdir()
    write_wav(src / "20261103-214329.wav", sine_pcm(1))
    (src / "20261103-214329.txt").write_text("γεια σου")
    return src


def digest(src):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(src.iterdir())}


def test_dry_run_writes_nothing(db, db_url, mk_settings, tmp_path, capsys):
    s = mk_settings(db_url); src = setup(tmp_path)
    assert import_v1.run(s, src, dry_run=True) == 0
    assert "would-import" in capsys.readouterr().out
    assert db.execute("SELECT count(*) FROM recording").fetchone()[0] == 0
    assert not (s.paths.data_dir / "recordings").exists()


def test_import_then_rerun_skips(db, db_url, mk_settings, tmp_path, capsys):
    s = mk_settings(db_url); src = setup(tmp_path); before = digest(src)
    assert import_v1.run(s, src) == 0
    r = db.execute("SELECT id, audio_path, started_at, source, sample_rate, channels, duration_ms FROM recording").fetchone()
    assert r[1].endswith("master.flac") and r[3]["import"] == "v1" and r[4] == 16000 and r[5] == 1
    assert abs(r[6] - 1000) <= 50 and r[2] == datetime.datetime(2026, 11, 3, 21, 43, 29, tzinfo=zoneinfo.ZoneInfo("Europe/Athens"))
    d = s.paths.data_dir / "recordings" / str(r[0])
    assert (d / "v1-transcript.txt").read_text() == "γεια σου" and (d / "master.flac").exists()
    capsys.readouterr()
    assert import_v1.run(s, src) == 0
    assert "skipped" in capsys.readouterr().out
    assert db.execute("SELECT count(*) FROM recording").fetchone()[0] == 1
    assert digest(src) == before


def test_corrupt_wav_failed_exit_1_others_import(db, db_url, mk_settings, tmp_path):
    s = mk_settings(db_url); src = setup(tmp_path)
    (src / "20261103-220000.wav").write_bytes(b"not audio at all")
    assert import_v1.run(s, src) == 1
    assert db.execute("SELECT count(*) FROM recording").fetchone()[0] == 1
