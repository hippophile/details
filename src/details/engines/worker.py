"""STT worker child: `python -m details.engines.worker <spec.json>`.
Spec: engine, model, audio_path, duration_s, window_s, overlap_s (optional, 0 = none), settings, out_dir, plugins. Appends one JSON line per finished
window to <out_dir>/segments.jsonl (flush + fsync), skips windows already there on resume, then writes
worker.done.json (NOT done.json: that name is the runner's step marker). With overlap_s a window is decoded
over [a - overlap, b + overlap] (clipped to the audio) and keeps the segments whose midpoint lies in [a, b)
(the last window keeps everything from a on). stdout is the heartbeat: one line at model
load ("loaded"), one per window ("window i/n")."""
import importlib, json, math, os, pathlib, sys

from . import get_engine


def window_plan(duration_s, window_s):
    n = max(1, math.ceil(duration_s / window_s - 1e-9))
    return [(i, i * window_s, min((i + 1) * window_s, duration_s)) for i in range(n)]


def own_segments(segs, a, b, last):
    """Segments whose midpoint is in [a, b) seconds (open end for the last window): each is kept by exactly one window."""
    return [s for s in segs if a * 1000 <= (s.start_ms + s.end_ms) / 2 and (last or (s.start_ms + s.end_ms) / 2 < b * 1000)]


def read_done(path):
    """idx -> record for the intact lines of the checkpoint; a torn last line is ignored."""
    done = {}
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return done
    for line in text.splitlines():
        try:
            rec = json.loads(line)
            done[rec["idx"]] = rec
        except (ValueError, KeyError, TypeError):
            continue
    return done


def main(argv):
    spec = json.loads(pathlib.Path(argv[0]).read_text(encoding="utf-8"))
    for mod in spec.get("plugins") or []:
        importlib.import_module(mod)
    try:
        cls = get_engine(spec["engine"])
    except KeyError as e:
        print(f"error: {e.args[0]}", file=sys.stderr)
        return 2
    out = pathlib.Path(spec["out_dir"])
    ck = out / "segments.jsonl"
    done = read_done(ck)
    plan = window_plan(spec["duration_s"], spec["window_s"])
    ov = spec.get("overlap_s") or 0
    eng = cls(spec["model"])
    eng.load()
    print(f"loaded {spec['engine']}", flush=True)
    # rewrite the checkpoint without a torn tail so appends start on a clean line
    ck.write_text("".join(json.dumps(done[i], ensure_ascii=False) + "\n" for i in sorted(done)), encoding="utf-8")
    with open(ck, "a", encoding="utf-8") as f:
        for i, a, b in plan:
            if i not in done:
                segs = eng.transcribe_window(spec["audio_path"], max(0, a - ov), min(spec["duration_s"], b + ov),
                                             spec["settings"])
                if ov:
                    segs = own_segments(segs, a, b, i == len(plan) - 1)
                rec = {"idx": i, "start_s": a, "end_s": b, "language": getattr(eng, "language", None),
                       "segments": [s.to_dict() for s in segs]}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            print(f"window {i + 1}/{len(plan)}", flush=True)
    (out / "worker.done.json").write_text(json.dumps({"windows": len(plan)}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
