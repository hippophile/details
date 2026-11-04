"""stt-runs: migration 0004, Greek postfix, engine registry, v1 adoption, library runs_count, run labels."""
import asyncio

import pytest
from psycopg.errors import CheckViolation, UniqueViolation

from details.db import open_pool
from details.engines import ENGINES, get_engine
from details.engines.base import Segment
from details.services import library, stt_runs
from stt_helpers import insert_run, insert_segments, rec
from library_helpers import seed


def cols(db, table):
    return {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s",
                                      (table,))}


def test_tables_have_the_spec_columns(db):
    assert {"id", "recording_id", "engine", "model_id", "model_revision", "settings", "language", "status",
            "started_at", "finished_at", "wall_ms", "audio_ms", "rtf", "error", "text", "raw", "created_at",
            "job_id"} <= cols(db, "stt_run")
    assert {"id", "stt_run_id", "idx", "start_ms", "end_ms", "text", "avg_logprob", "no_speech_prob",
            "words"} <= cols(db, "transcript_segment")


def test_segment_idx_unique_per_run_and_status_checked(db, mk_settings):
    rid = rec(db, mk_settings())
    run = insert_run(db, rid)
    insert_segments(db, run, 1)
    with pytest.raises(UniqueViolation):
        insert_segments(db, run, 1)
    with pytest.raises(CheckViolation):
        insert_run(db, rid, status="bogus")


def test_deleting_recording_cascades_runs_and_segments(db, mk_settings):
    rid = rec(db, mk_settings())
    run = insert_run(db, rid)
    insert_segments(db, run, 10)
    db.execute("DELETE FROM recording WHERE id=%s", (rid,))
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM transcript_segment").fetchone()[0] == 0


def test_rerun_is_a_new_row_and_leaves_the_first_alone(db, mk_settings):
    rid = rec(db, mk_settings())
    a = insert_run(db, rid, text="first")
    insert_segments(db, a, 2)
    insert_run(db, rid, text="second")
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 2
    assert db.execute("SELECT text FROM stt_run WHERE id=%s", (a,)).fetchone()[0] == "first"
    assert db.execute("SELECT count(*) FROM transcript_segment WHERE stt_run_id=%s", (a,)).fetchone()[0] == 2


# ---- Greek postfix -------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,want", [
    ("Τι κάνεις?", "Τι κάνεις;"),
    ("Is it ok?", "Is it ok?"),
    ("Gemma?", "Gemma?"),
    ("Τι είναι το RAG?", "Τι είναι το RAG?"),          # heuristic: a ? after a Latin word stays
    ("Ποιος? Εσύ? Ναι.", "Ποιος; Εσύ; Ναι."),
    ("Τι κάνεις ?", "Τι κάνεις ?"),                       # only a ? DIRECTLY after a Greek letter
    ("", ""),
])
def test_greek_postfix(text, want):
    assert stt_runs.greek_postfix(text) == want


# ---- engine registry -----------------------------------------------------------------------------------

def test_registry_has_mlx_whisper_and_resolves_names():
    assert "mlx_whisper" in ENGINES
    assert get_engine("mlx_whisper").name == "mlx_whisper"
    with pytest.raises(KeyError, match="nope"):
        get_engine("nope")


def test_registered_fake_engine_is_resolvable():
    import fakes.stt    # registers "fake" on import
    eng = get_engine("fake")({"repo": "x"})
    segs = eng.transcribe_window("a.flac", 2, 3, {"language": "el"})
    assert isinstance(segs[0], Segment) and segs[0].start_ms == 2000 and segs[0].end_ms == 3000
    assert segs[0].to_dict()["text"] == "w2 Τι κάνεις?"


# ---- v1 adoption ---------------------------------------------------------------------------------------

def adopt(db_url, s, rid=None):
    async def go():
        pool = await open_pool(db_url)
        try:
            return await stt_runs.adopt_v1_transcripts(pool, s, rid)
        finally:
            await pool.close()
    return asyncio.run(go())


def test_adopt_once_and_idempotent(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = rec(db, s)
    (s.paths.data_dir / "recordings" / rid / "v1-transcript.txt").write_text("Καλημέρα κόσμε", encoding="utf-8")
    adopt(db_url, s)
    adopt(db_url, s)
    rows = db.execute("SELECT engine, model_id, status, text FROM stt_run").fetchall()
    assert rows == [("mlx_whisper", "unknown (v1 import)", "done", "Καλημέρα κόσμε")]
    assert db.execute("SELECT count(*) FROM transcript_segment").fetchone()[0] == 0


def test_adopt_without_file_creates_nothing(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rec(db, s)
    adopt(db_url, s)
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 0


def test_adopt_skips_when_a_v1_run_exists_even_if_other_runs_do(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = rec(db, s)
    (s.paths.data_dir / "recordings" / rid / "v1-transcript.txt").write_text("x")
    insert_run(db, rid, model_id="whisper-large", text="real")
    adopt(db_url, s)
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 2


# ---- library runs_count --------------------------------------------------------------------------------

def test_library_list_runs_count(db, db_url, mk_settings):
    s = mk_settings(db_url)
    a, b = rec(db, s, title="two"), rec(db, s, title="none")
    insert_run(db, a)
    insert_run(db, a, model_id="whisper-turbo")

    async def go():
        pool = await open_pool(db_url)
        try:
            return {r["title"]: r["runs_count"] for r in await library.list_items(pool)}, \
                   (await library.get_item(pool, a))["runs_count"]
        finally:
            await pool.close()
    counts, one = asyncio.run(go())
    assert counts == {"two": 2, "none": 0} and one == 2


# ---- labels --------------------------------------------------------------------------------------------

def test_label_has_model_wall_and_rtf():
    lab = stt_runs.run_label(3, "Whisper large-v3", "done", 9700, 7.94)
    assert "Whisper large-v3" in lab and "9.7 s" in lab and "7.9×" in lab and lab.startswith("#3")
    assert "queued" in stt_runs.run_label(1, "Whisper large-v3", "queued", None, None)
