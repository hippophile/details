"""ffmpeg / ffprobe wrappers (blocking; call via asyncio.to_thread from the server)."""
import hashlib, json, os, pathlib, shutil, subprocess


class AudioError(RuntimeError):
    pass


def find_tool(configured, name):
    """Configured path must exist; empty means look up `name` on PATH. Returns the path or None."""
    if configured:
        p = os.path.expanduser(configured)
        return p if os.path.isfile(p) and os.access(p, os.X_OK) else None
    return shutil.which(name)


def probe(ffprobe, path):
    """-> {duration_s, sample_rate, channels}; AudioError if unreadable."""
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=sample_rate,channels:format=duration", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise AudioError(f"ffprobe failed: {r.stderr.strip()[:200]}")
        j = json.loads(r.stdout)
        st = j["streams"][0]
        return {"duration_s": float(j["format"]["duration"]), "sample_rate": int(st["sample_rate"]),
                "channels": int(st["channels"])}
    except AudioError:
        raise
    except Exception as e:
        raise AudioError(f"ffprobe failed: {type(e).__name__}") from None


def to_flac(ffmpeg, src, dst):
    """Convert to FLAC via a .part file renamed on success."""
    dst = pathlib.Path(dst)
    part = dst.with_name(dst.name + ".part")
    try:
        r = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(src), "-c:a", "flac", "-f", "flac", str(part)],
                           capture_output=True, text=True, timeout=3600)
        if r.returncode or not part.exists():
            raise AudioError(f"ffmpeg exited {r.returncode}: {r.stderr.strip()[:200]}")
        os.replace(part, dst)
    except AudioError:
        part.unlink(missing_ok=True)
        raise
    except Exception as e:
        part.unlink(missing_ok=True)
        raise AudioError(f"ffmpeg failed: {type(e).__name__}") from None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
