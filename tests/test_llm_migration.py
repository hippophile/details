"""llm-outputs: migration 0005."""
import pytest
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from stt_helpers import insert_run, rec


def cols(db, table):
    return {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s",
                                      (table,))}


def test_tables_have_the_spec_columns_and_no_tag_tables(db):
    assert {"id", "name", "version", "kind", "body", "output_schema", "sha256", "created_at"} <= cols(db, "prompt_template")
    assert {"id", "group_id", "stt_run_id", "kind", "prompt_template_id", "llm_endpoint", "model_id", "params",
            "output_language", "input_text_sha256", "rendered_prompt", "status", "started_at", "finished_at", "wall_ms",
            "tokens_in", "tokens_out", "tok_per_s", "finish_reason", "raw_response", "schema_valid",
            "validation_errors", "retry_of", "error", "created_at"} <= cols(db, "analysis_run")
    assert {"id", "analysis_run_id", "kind", "schema_version", "content"} <= cols(db, "output")
    names = {r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert not {"tag", "tags", "checklist_item"} & names


def make_run(db, settings):
    rid = rec(db, settings)
    run = insert_run(db, rid)
    return db.execute("INSERT INTO analysis_run (group_id, stt_run_id, kind, model_id) "
                      "VALUES (gen_random_uuid(), %s, 'tags', 'm') RETURNING id", (run,)).fetchone()[0], run


def test_output_unique_per_run_and_cascades_from_stt_run(db, mk_settings):
    ar, run = make_run(db, mk_settings())
    db.execute("INSERT INTO output (analysis_run_id, kind, schema_version, content) VALUES (%s,'tags',1,%s)",
               (ar, Jsonb({"tags": []})))
    with pytest.raises(UniqueViolation):
        db.execute("INSERT INTO output (analysis_run_id, kind, schema_version, content) VALUES (%s,'tags',1,%s)",
                   (ar, Jsonb({})))
    db.execute("DELETE FROM stt_run WHERE id=%s", (run,))
    assert db.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM output").fetchone()[0] == 0


def test_status_check_and_retry_link(db, mk_settings):
    from psycopg.errors import CheckViolation
    ar, run = make_run(db, mk_settings())
    with pytest.raises(CheckViolation):
        db.execute("UPDATE analysis_run SET status='bogus' WHERE id=%s", (ar,))
    db.execute("INSERT INTO analysis_run (group_id, stt_run_id, kind, model_id, retry_of) "
               "SELECT group_id, stt_run_id, kind, model_id, id FROM analysis_run WHERE id=%s", (ar,))
    assert db.execute("SELECT count(*) FROM analysis_run WHERE retry_of=%s", (ar,)).fetchone()[0] == 1
