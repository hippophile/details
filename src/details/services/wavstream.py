"""Crash-safe WAV appending: 44-byte header patched + fsynced periodically, atomic meta.json, header repair."""
import json, os, pathlib, struct, time

HEADER = 44


def header(sample_rate, data_len, channels=1):
    return (b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, sample_rate * channels * 2, channels * 2, 16) +
            b"data" + struct.pack("<I", data_len))


def _patch(f, data_len):
    pos = f.tell()
    f.seek(4); f.write(struct.pack("<I", 36 + data_len))
    f.seek(40); f.write(struct.pack("<I", data_len))
    f.seek(pos)


class WavWriter:
    """Mono int16 appender. An odd trailing byte is held and prepended to the next write."""

    def __init__(self, path, sample_rate, sync_interval_s=5.0):
        self.path = pathlib.Path(path)
        self.sample_rate = sample_rate
        self.sync_interval_s = sync_interval_s
        self._f = open(self.path, "wb")
        self._f.write(header(sample_rate, 0))
        self._f.flush()
        self._data = 0
        self._held = b""
        self._last_sync = time.monotonic()

    @property
    def frames(self):
        return self._data // 2

    def write(self, chunk):
        chunk = self._held + bytes(chunk)
        if len(chunk) % 2:
            chunk, self._held = chunk[:-1], chunk[-1:]
        else:
            self._held = b""
        if chunk:
            self._f.write(chunk)
            self._data += len(chunk)
        if time.monotonic() - self._last_sync >= self.sync_interval_s:
            self.sync()

    def sync(self):
        if self._f.closed:
            return
        _patch(self._f, self._data)
        self._f.flush()
        os.fsync(self._f.fileno())
        self._last_sync = time.monotonic()

    def close(self):
        if not self._f.closed:
            self.sync()
            self._f.close()


def repair(path):
    """Fix the header from the file size (truncating to whole frames). Returns the frame count."""
    p = pathlib.Path(path)
    size = p.stat().st_size
    if size < HEADER:
        return 0
    data = (size - HEADER) // 2 * 2
    with open(p, "r+b") as f:
        f.truncate(HEADER + data)
        _patch(f, data)
        f.flush()
        os.fsync(f.fileno())
    return data // 2


def write_meta(directory, meta):
    d = pathlib.Path(directory)
    tmp = d / "meta.json.tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, d / "meta.json")


def read_meta(directory):
    try:
        return json.loads((pathlib.Path(directory) / "meta.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
