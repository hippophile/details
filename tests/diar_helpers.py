"""Helpers for the speakers-diarization tests: a seeded recording with a done transcript, diarization run rows,
env for the fake diarization worker child, fake Hugging Face hubs (complete / partial snapshot). Fakes only, no torch."""
import asyncio, json, pathlib, types

from psycopg.types.json import Jsonb

from details.services import diarization
from details.settings import REPO_ROOT
from stt_helpers import insert_run, rec

SRC, TESTS = REPO_ROOT / "src", pathlib.Path(__file__).resolve().parent
REPO_DIR = "models--pyannote--speaker-diarization-community-1"
SENTINEL = "hf_SENTINELabcdefghijklmnopqrstuvwxyz0123456789"
TOKEN_NAMES = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "DETAILS_DIARIZATION_HF_TOKEN")
PLUGIN = {"DETAILS_DIARIZATION_PLUGINS": "fakes.diar"}

# (start_ms, end_ms, text): 7 segments over a 70 s recording; the last one starts at 65 s (click-to-seek target)
SEGMENTS = [(0, 9000, "Καλημέρα σε όλους."), (10000, 19000, "Θα κάνουμε deploy στις 12 Νοεμβρίου."),
            (21000, 30000, "Ο Νίκος θα ετοιμάσει το documentation."), (31000, 44000, "Υπάρχει ρίσκο με το GDPR."),
            (46000, 55000, "Δεν ξέρουμε ακόμα ποιο μοντέλο."), (56000, 64000, "Ας το δούμε την Παρασκευή."),
            (65000, 69000, "Τέλος της συνάντησης.")]
# raw engine turns: SPEAKER_01 first -> speaker 1, SPEAKER_00 -> 2, SPEAKER_02 -> 3
TURNS = [("SPEAKER_01", 0, 20000), ("SPEAKER_00", 20000, 45000), ("SPEAKER_02", 45000, 70000)]


def seed_transcript(db, settings, segments=SEGMENTS, seconds=70, title="rec", language="el"):
    """-> (recording_id, stt_run_id): a recording of `seconds` with a done stt_run and its segments."""
    rid = rec(db, settings, seconds=seconds, title=title)
    run_id = insert_run(db, rid, status="done", language=language, text=" ".join(t for _, _, t in segments))
    for i, (a, b, t) in enumerate(segments):
        db.execute("INSERT INTO transcript_segment (stt_run_id, idx, start_ms, end_ms, text) VALUES (%s,%s,%s,%s,%s)",
                   (run_id, i, a, b, t))
    return rid, run_id


def insert_diar_run(db, rid, status="done", turns=TURNS, names=None, audio_ms=70000, merge_gap_ms=1500, **cols):
    """-> diarization_run id (str). `turns` are raw (label, start_ms, end_ms); they go through the real normalise +
    stats functions, so a stored run looks like one the step wrote. `names` = {speaker: name}."""
    norm = diarization.normalise_turns([{"speaker": s, "start_ms": a, "end_ms": b} for s, a, b in turns], audio_ms)
    speakers = diarization.stats(norm, merge_gap_ms) if status == "done" else None
    row = {"recording_id": rid, "engine": "pyannote", "model_id": "community-1", "status": status,
           "audio_ms": audio_ms, "wall_ms": 192000, "settings": Jsonb({"device": "cpu", "merge_gap_ms": merge_gap_ms}),
           "speakers": Jsonb(speakers) if speakers is not None else None, **cols}
    names_sql = ", ".join(row)
    run_id = str(db.execute(f"INSERT INTO diarization_run ({names_sql}) VALUES ({', '.join(['%s'] * len(row))}) "
                            "RETURNING id", list(row.values())).fetchone()[0])
    if status == "done":
        for i, t in enumerate(norm):
            db.execute("INSERT INTO diar_turn (diarization_run_id, idx, start_ms, end_ms, speaker) VALUES (%s,%s,%s,%s,%s)",
                       (run_id, i, t["start_ms"], t["end_ms"], t["speaker"]))
    for n, name in (names or {}).items():
        db.execute("INSERT INTO speaker_name (diarization_run_id, speaker, name) VALUES (%s,%s,%s)", (run_id, n, name))
    return run_id


def fake_diar_env(monkeypatch, tmp_path, override=True, **knobs):
    """The diar worker child inherits this env: it finds `details` + the fakes. -> namespace of the dump files."""
    monkeypatch.setenv("PYTHONPATH", f"{SRC}:{TESTS}")
    if override:
        monkeypatch.setenv("FAKE_DIAR_OVERRIDE", "1")
    paths = types.SimpleNamespace(calls=tmp_path / "diar_calls.jsonl", envfile=tmp_path / "diar_env.json",
                                  pidfile=tmp_path / "diar_pid")
    monkeypatch.setenv("FAKE_DIAR_CALLS", str(paths.calls))
    monkeypatch.setenv("FAKE_DIAR_ENVFILE", str(paths.envfile))
    for k, v in knobs.items():
        monkeypatch.setenv(f"FAKE_DIAR_{k.upper()}", str(v))
    return paths


def diar_ready(monkeypatch):
    """The package check says yes (the real one is False in a venv without pyannote)."""
    from details.services import models
    monkeypatch.setattr(models, "pyannote_importable", lambda: True)


def diar_hub(tmp_path, state="complete"):
    """A Hugging Face hub folder with the community-1 snapshot: complete (config + weights), partial (config only)
    or missing (no snapshot at all)."""
    hub = tmp_path / "hub"
    hub.mkdir(exist_ok=True)
    if state != "missing":
        snap = hub / REPO_DIR / "snapshots" / "abc"
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "config.yaml").write_text("version: 4.0.0\n")
        if state == "complete":
            (snap / "segmentation").mkdir(exist_ok=True)
            (snap / "segmentation" / "pytorch_model.bin").write_bytes(b"x")
    return hub


def run(coro_fn, timeout=40):
    return asyncio.run(asyncio.wait_for(coro_fn(), timeout))


def lines(path):
    return [json.loads(x) for x in pathlib.Path(path).read_text().splitlines() if x.strip()] \
        if pathlib.Path(path).exists() else []


async def queue_run(e, rid, status="queued", **run_settings):
    """A queued diarization_run + its `diarize` job, without going through the API. -> (run_id, job_id)."""
    async with e.pool.connection() as c:
        row = await (await c.execute("INSERT INTO diarization_run (recording_id, engine, model_id, settings, status) "
                                     "VALUES (%s,'pyannote','community-1',%s,%s) RETURNING id",
                                     (rid, Jsonb({"device": e.settings.diarization.device,
                                                  "merge_gap_ms": e.settings.diarization.merge_gap_ms,
                                                  **run_settings}), status))).fetchone()
        run_id = str(row["id"])
    job_id = await e.svc.enqueue("diarize", rid, [{"name": "diarize", "params": {"diarization_run_id": run_id}}])
    async with e.pool.connection() as c:
        await c.execute("UPDATE diarization_run SET job_id=%s WHERE id=%s", (job_id, run_id))
    return run_id, job_id


def drow(db, run_id):
    return db.execute("SELECT status, error, started_at, wall_ms, audio_ms, rtf, speakers FROM diarization_run WHERE id=%s",
                      (run_id,)).fetchone()
