"""step.json: the per-step record next to done.json (<data_dir>/jobs/<job_id>/<step>/step.json), written atomically.
`view()` turns a job's records into the list served by /api/jobs/{id}/steps and the SSE `steps` field."""
import json, os, pathlib

from .joblog import redact

KEYS = ("index", "name", "status", "started_at", "finished_at", "duration_ms", "error", "model", "tokens_in",
        "tokens_out", "tok_per_s", "ram_free_start_gb", "ram_free_end_gb")
METRICS = ("model", "tokens_in", "tokens_out", "tok_per_s")
_CACHE = {}       # path -> (mtime_ns, size, record): a file is re-read only when it changed


def write(step_dir, rec, secrets=()):
    step_dir = pathlib.Path(step_dir)
    rec = {k: rec.get(k) for k in KEYS}
    rec["error"] = redact(rec["error"], secrets) if rec["error"] else None
    tmp = step_dir / "step.json.tmp"
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, step_dir / "step.json")


def read(step_dir):
    path = pathlib.Path(step_dir) / "step.json"
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    hit = _CACHE.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return dict(hit[2])
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    if len(_CACHE) > 2000:
        _CACHE.clear()
    _CACHE[key] = (st.st_mtime_ns, st.st_size, rec)
    return dict(rec)


def view(data_dir, job, secrets=()):
    """-> one dict per step of the job. Not yet reached = `pending`; a running record on a job that is not running
    = `interrupted`; a step skipped by its done.json keeps its old record (or shows done)."""
    root = pathlib.Path(data_dir) / "jobs" / str(job["id"])
    job_live = job["status"] in ("running", "cancelling")
    out = []
    for i, spec in enumerate(job["steps"]):
        name = spec["name"]
        rec = read(root / name)
        if rec is not None and rec.get("index") not in (None, i):
            rec = None                           # a record from an older run of a differently shaped job
        item = {k: None for k in KEYS}
        item.update(index=i, name=name, status="pending")
        if rec is not None:
            item.update({k: rec.get(k) for k in KEYS})
            item.update(index=i, name=name)
            st = item["status"]
            if st in ("running", "waiting_gpu") and not job_live:
                item["status"] = "interrupted"
            elif st in ("failed", "cancelled") and job["status"] == "queued" and i >= job["step_index"]:
                item["status"] = "pending"       # a retry is queued: this step will run again
        elif (root / name / "done.json").is_file():
            item["status"] = "done"
        if item["error"]:
            item["error"] = redact(item["error"], secrets)
        out.append(item)
    return out
