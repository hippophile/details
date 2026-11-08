"""speakers-diarization: migration 0009 (diarization_run, diar_turn, speaker_name, analysis_run.diarization_run_id)."""
import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

from diar_helpers import insert_diar_run, seed_transcript
from stt_helpers import rec


def cols(db, table):
    return {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table,))}


def count(db, table):
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_tables_have_the_spec_columns(db):
    assert {"id", "recording_id", "job_id", "engine", "model_id", "settings", "status", "started_at", "finished_at",
            "wall_ms", "audio_ms", "rtf", "speakers", "error", "created_at"} <= cols(db, "diarization_run")
    assert {"id", "diarization_run_id", "idx", "start_ms", "end_ms", "speaker"} <= cols(db, "diar_turn")
    assert {"diarization_run_id", "speaker", "name"} <= cols(db, "speaker_name")
    assert "diarization_run_id" in cols(db, "analysis_run")
    assert "stt_run_id" not in cols(db, "diarization_run")            # diarization belongs to the recording


def test_rerun_creates_a_new_row_and_leaves_the_first_unchanged(db, mk_settings, db_url):
    rid = rec(db, mk_settings(db_url))
    first = insert_diar_run(db, rid)
    before = db.execute("SELECT status, speakers FROM diarization_run WHERE id=%s", (first,)).fetchone()
    second = insert_diar_run(db, rid)
    assert first != second and count(db, "diarization_run") == 2
    assert db.execute("SELECT status, speakers FROM diarization_run WHERE id=%s", (first,)).fetchone() == before
    assert db.execute("SELECT count(*) FROM diar_turn WHERE diarization_run_id=%s", (first,)).fetchone()[0] == 3


def test_deleting_the_recording_cascades_to_runs_turns_and_names(db, mk_settings, db_url):
    rid = rec(db, mk_settings(db_url))
    insert_diar_run(db, rid, names={1: "Ελένη"})
    assert count(db, "diar_turn") == 3 and count(db, "speaker_name") == 1
    db.execute("DELETE FROM recording WHERE id=%s", (rid,))
    assert (count(db, "diarization_run"), count(db, "diar_turn"), count(db, "speaker_name")) == (0, 0, 0)


def test_deleting_a_run_keeps_analyses_with_the_column_set_to_null(db, mk_settings, db_url):
    rid, run_id = seed_transcript(db, mk_settings(db_url))
    dr = insert_diar_run(db, rid)
    aid = db.execute("INSERT INTO analysis_run (group_id, stt_run_id, kind, model_id, diarization_run_id) "
                     "VALUES (gen_random_uuid(), %s, 'tags', 'm', %s) RETURNING id", (run_id, dr)).fetchone()[0]
    db.execute("DELETE FROM diarization_run WHERE id=%s", (dr,))
    assert db.execute("SELECT diarization_run_id FROM analysis_run WHERE id=%s", (aid,)).fetchone() == (None,)


def test_analysis_run_column_is_nullable_with_no_default(db, mk_settings, db_url):
    _, run_id = seed_transcript(db, mk_settings(db_url))
    aid = db.execute("INSERT INTO analysis_run (group_id, stt_run_id, kind, model_id) "
                     "VALUES (gen_random_uuid(), %s, 'tags', 'm') RETURNING id", (run_id,)).fetchone()[0]
    assert db.execute("SELECT diarization_run_id FROM analysis_run WHERE id=%s", (aid,)).fetchone() == (None,)


def test_status_check(db, mk_settings, db_url):
    rid = rec(db, mk_settings(db_url))
    for ok in ("queued", "running", "done", "failed", "cancelled"):
        db.execute("INSERT INTO diarization_run (recording_id, engine, model_id, status) VALUES (%s,'pyannote','m',%s)",
                   (rid, ok))
    with pytest.raises(CheckViolation):
        db.execute("INSERT INTO diarization_run (recording_id, engine, model_id, status) VALUES (%s,'pyannote','m','weird')",
                   (rid,))


def test_default_status_is_queued_and_recording_fk(db, mk_settings, db_url):
    rid = rec(db, mk_settings(db_url))
    got = db.execute("INSERT INTO diarization_run (recording_id, engine, model_id) VALUES (%s,'pyannote','m') "
                     "RETURNING status", (rid,)).fetchone()
    assert got == ("queued",)
    with pytest.raises(ForeignKeyViolation):
        db.execute("INSERT INTO diarization_run (recording_id, engine, model_id) "
                   "VALUES (gen_random_uuid(),'pyannote','m')")


@pytest.mark.parametrize("a,b,speaker", [(1000, 1000, 1), (2000, 1000, 1), (0, 1000, 0)])
def test_invalid_turns_refused(db, mk_settings, db_url, a, b, speaker):
    rid = rec(db, mk_settings(db_url))
    dr = insert_diar_run(db, rid, turns=[])
    with pytest.raises(CheckViolation):
        db.execute("INSERT INTO diar_turn (diarization_run_id, idx, start_ms, end_ms, speaker) VALUES (%s,0,%s,%s,%s)",
                   (dr, a, b, speaker))


def test_turn_idx_is_unique_per_run(db, mk_settings, db_url):
    rid = rec(db, mk_settings(db_url))
    dr = insert_diar_run(db, rid)
    with pytest.raises(UniqueViolation):
        db.execute("INSERT INTO diar_turn (diarization_run_id, idx, start_ms, end_ms, speaker) VALUES (%s,0,1,2,1)", (dr,))


def test_speaker_name_primary_key_is_run_and_speaker(db, mk_settings, db_url):
    rid = rec(db, mk_settings(db_url))
    a, b = insert_diar_run(db, rid, names={1: "Ελένη"}), insert_diar_run(db, rid, names={1: "Ελένη"})   # same speaker no. in two runs
    assert a != b
    with pytest.raises(UniqueViolation):
        db.execute("INSERT INTO speaker_name (diarization_run_id, speaker, name) VALUES (%s,1,'Άλλη')", (a,))
    with pytest.raises(CheckViolation):
        db.execute("INSERT INTO speaker_name (diarization_run_id, speaker, name) VALUES (%s,2,'   ')", (a,))
    with pytest.raises(CheckViolation):
        db.execute("INSERT INTO speaker_name (diarization_run_id, speaker, name) VALUES (%s,0,'x')", (a,))
