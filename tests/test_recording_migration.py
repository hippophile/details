import psycopg
import pytest


def cols(db, table):
    return {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns "
                                     "WHERE table_name=%s", (table,))}


def test_tables_and_columns(db):
    assert {"id", "slug", "name", "body_md", "sha256", "sort_order", "created_at"} <= cols(db, "script")
    assert {"id", "title", "started_at", "duration_ms", "audio_path", "audio_sha256", "container", "codec",
            "sample_rate", "channels", "channel_map", "source", "script_id", "notes", "size_bytes",
            "deleted_at", "created_at"} <= cols(db, "recording")
    v = [r[0] for r in db.execute("SELECT version FROM schema_migration ORDER BY version")]
    assert 2 in v


def test_only_two_tables_added(db):
    """0002 adds script + recording; later migrations add their own tables (job: 0003, stt_run + transcript_segment: 0004, prompt_template + analysis_run + output: 0005, comparison + comparison_item + eval_result: 0006)."""
    names = {r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert {"script", "recording"} <= names and names <= {"schema_migration", "script", "recording", "job", "stt_run", "transcript_segment",
                                                                     "prompt_template", "analysis_run", "output", "comparison", "comparison_item",
                                                                     "eval_result"}


def test_slug_unique(db):
    ins = "INSERT INTO script (slug, name, body_md, sha256, sort_order) VALUES ('standup','a','b','c',1)"
    db.execute(ins)
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(ins)


def test_script_fk(db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("INSERT INTO recording (title, started_at, duration_ms, audio_path, container, codec, "
                   "sample_rate, channels, script_id) VALUES ('t', now(), 1, 'p', 'flac', 'flac', 16000, 1, 999)")
