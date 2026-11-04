"""compare-runs: migration 0006 (cascades, side PK, unique eval key)."""
import pytest
from psycopg.errors import CheckViolation, UniqueViolation
from psycopg.types.json import Jsonb

from compare_helpers import add_script, seed_rec, seed_stt


def cols(db, table):
    return {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s",
                                      (table,))}


def mk(db, mk_settings):
    rid = seed_rec(db, mk_settings())
    a, b = seed_stt(db, rid), seed_stt(db, rid, model_id="whisper-large")
    cid = db.execute("INSERT INTO comparison (recording_id) VALUES (%s) RETURNING id", (rid,)).fetchone()[0]
    for side, run in (("A", a), ("B", b)):
        db.execute("INSERT INTO comparison_item (comparison_id, side, stt_run_id, label) VALUES (%s,%s,%s,'x')",
                   (cid, side, run))
    return rid, a, b, cid


def test_tables_have_the_spec_columns(db):
    assert {"id", "recording_id", "created_at"} <= cols(db, "comparison")
    assert {"comparison_id", "side", "stt_run_id", "analysis_group_id", "label"} <= cols(db, "comparison_item")
    assert {"id", "stt_run_id", "script_id", "script_sha256", "metrics", "created_at"} <= cols(db, "eval_result")


def test_side_is_primary_key_and_checked(db, mk_settings):
    rid, a, b, cid = mk(db, mk_settings)
    with pytest.raises(UniqueViolation):
        db.execute("INSERT INTO comparison_item (comparison_id, side, stt_run_id, label) VALUES (%s,'A',%s,'x')",
                   (cid, b))
    with pytest.raises(CheckViolation):
        db.execute("INSERT INTO comparison_item (comparison_id, side, stt_run_id, label) VALUES (%s,'C',%s,'x')",
                   (cid, b))


def test_deleting_a_run_cascades_its_item_deleting_the_recording_cascades_all(db, mk_settings):
    rid, a, b, cid = mk(db, mk_settings)
    db.execute("DELETE FROM stt_run WHERE id=%s", (a,))
    assert db.execute("SELECT count(*) FROM comparison_item WHERE comparison_id=%s", (cid,)).fetchone()[0] == 1
    db.execute("DELETE FROM recording WHERE id=%s", (rid,))
    assert db.execute("SELECT count(*) FROM comparison").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM comparison_item").fetchone()[0] == 0


def test_eval_result_unique_per_run_and_script_hash_cascades_from_run_and_nulls_script(db, mk_settings):
    sid, s = add_script(db)
    rid = seed_rec(db, mk_settings(), sid)
    run = seed_stt(db, rid)
    ins = "INSERT INTO eval_result (stt_run_id, script_id, script_sha256, metrics) VALUES (%s,%s,%s,%s)"
    db.execute(ins, (run, sid, s["sha256"], Jsonb({})))
    with pytest.raises(UniqueViolation):
        db.execute(ins, (run, sid, s["sha256"], Jsonb({})))
    db.execute(ins, (run, sid, "other", Jsonb({})))                    # another script hash is fine
    db.execute("UPDATE recording SET script_id=NULL WHERE id=%s", (rid,))
    db.execute("DELETE FROM script WHERE id=%s", (sid,))
    assert db.execute("SELECT count(*) FROM eval_result WHERE script_id IS NULL").fetchone()[0] == 2
    db.execute("DELETE FROM stt_run WHERE id=%s", (run,))
    assert db.execute("SELECT count(*) FROM eval_result").fetchone()[0] == 0
