import wave

from details.services import wavstream
from recording_helpers import SR, raw_wav_stale, sine_pcm


def frames(path):
    with wave.open(str(path)) as w:
        return w.getnframes(), w.getframerate(), w.getnchannels()


def test_header_patch_makes_wave_read_n_frames(tmp_path):
    p = tmp_path / "rec.wav"
    w = wavstream.WavWriter(p, SR, sync_interval_s=3600)
    w.write(sine_pcm(1))
    assert w.frames == SR
    w.sync()
    assert frames(p) == (SR, SR, 1)
    w.close()
    assert frames(p) == (SR, SR, 1)


def test_odd_byte_is_held_then_prepended(tmp_path):
    p = tmp_path / "rec.wav"
    w = wavstream.WavWriter(p, SR, sync_interval_s=3600)
    data = sine_pcm(0.1)
    w.write(data[:101])            # odd length: last byte held back
    assert w.frames == 50
    w.write(data[101:])
    w.close()
    assert p.read_bytes()[44:] == data


def test_trailing_odd_byte_dropped_on_close(tmp_path):
    p = tmp_path / "rec.wav"
    w = wavstream.WavWriter(p, SR, sync_interval_s=3600)
    w.write(sine_pcm(0.1) + b"\x01")
    w.close()
    assert frames(p)[0] == 1600


def test_periodic_sync_patches_header_without_close(tmp_path):
    p = tmp_path / "rec.wav"
    w = wavstream.WavWriter(p, SR, sync_interval_s=0)   # every write syncs
    w.write(sine_pcm(0.2))
    assert frames(p)[0] == 3200                          # readable while writer is still open (as after kill)


def test_repair_stale_header_and_odd_tail(tmp_path):
    p = tmp_path / "rec.wav"
    raw_wav_stale(p, sine_pcm(3), tail=b"\x07")
    n = wavstream.repair(p)
    assert n == 3 * SR
    assert frames(p) == (3 * SR, SR, 1)
    assert p.stat().st_size == 44 + 3 * SR * 2          # torn byte truncated


def test_repair_empty_file_returns_zero(tmp_path):
    p = tmp_path / "rec.wav"
    raw_wav_stale(p, b"")
    assert wavstream.repair(p) == 0


def test_meta_written_atomically(tmp_path):
    wavstream.write_meta(tmp_path, {"status": "recording"})
    wavstream.write_meta(tmp_path, {"status": "done"})
    assert wavstream.read_meta(tmp_path) == {"status": "done"}
    assert [x.name for x in tmp_path.iterdir()] == ["meta.json"]
