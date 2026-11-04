import shutil

import pytest

import conftest
from details.db import connect
from details.migrate import MigrationError, migrate


def count(c, table="schema_migration"):
    return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_applies_in_order_and_skips_second_run(blank_db, tmp_path):
    d = tmp_path / "m"
    shutil.copytree(conftest.MIGRATIONS, d, ignore=shutil.ignore_patterns("0002_*", "0003_*", "0004_*", "0005_*", "0006_*"))
    (d / "0002_a.sql").write_text("CREATE TABLE a (id serial primary key);")
    (d / "0003_b.sql").write_text("CREATE TABLE b (id serial primary key);")
    with connect(blank_db) as c:
        assert migrate(c, d) == ["0001_schema_migration", "0002_a", "0003_b"]
        assert migrate(c, d) == []
        rows = c.execute("SELECT version, name FROM schema_migration ORDER BY version").fetchall()
        assert rows == [(1, "0001_schema_migration"), (2, "0002_a"), (3, "0003_b")]


def test_foundation_migration_applies_once(blank_db):
    with connect(blank_db) as c:
        migrate(c, conftest.MIGRATIONS)
        migrate(c, conftest.MIGRATIONS)
        assert c.execute("SELECT count(*) FROM schema_migration WHERE version = 1").fetchone()[0] == 1


def test_changed_applied_file_fails_and_applies_nothing(blank_db, tmp_path):
    d = tmp_path / "m"
    shutil.copytree(conftest.MIGRATIONS, d, ignore=shutil.ignore_patterns("0002_*", "0003_*", "0004_*", "0005_*", "0006_*"))
    with connect(blank_db) as c:
        migrate(c, d)
        (d / "0001_schema_migration.sql").write_text((d / "0001_schema_migration.sql").read_text() + "\n-- edit\n")
        (d / "0002_later.sql").write_text("CREATE TABLE later (id int);")
        with pytest.raises(MigrationError, match="0001_schema_migration"):
            migrate(c, d)
        assert c.execute("SELECT to_regclass('later')").fetchone()[0] is None


def test_failed_migration_rolls_back(blank_db, tmp_path):
    d = tmp_path / "m"
    shutil.copytree(conftest.MIGRATIONS, d, ignore=shutil.ignore_patterns("0002_*", "0003_*", "0004_*", "0005_*", "0006_*"))
    (d / "0002_bad.sql").write_text("CREATE TABLE half (id int); SELECT * FROM no_such_table;")
    with connect(blank_db) as c:
        with pytest.raises(Exception):
            migrate(c, d)
        assert c.execute("SELECT to_regclass('half')").fetchone()[0] is None
        assert count(c) == 1  # only 0001


def test_reset_refuses_non_test_database():
    with pytest.raises(RuntimeError, match="_test"):
        conftest.reset_test_db("postgresql://u:p@127.0.0.1:5432/details")


def test_reset_drops_leftover_tables(blank_db):
    with connect(blank_db) as c:
        c.execute("CREATE TABLE leftover (id int)")
    conftest.reset_test_db(blank_db)
    with connect(blank_db) as c:
        assert c.execute("SELECT to_regclass('leftover')").fetchone()[0] is None


def test_truncate_all_keeps_schema_migration_and_restarts_identity(blank_db, tmp_path):
    d = tmp_path / "m"
    shutil.copytree(conftest.MIGRATIONS, d, ignore=shutil.ignore_patterns("0002_*", "0003_*", "0004_*", "0005_*", "0006_*"))
    (d / "0002_t.sql").write_text("CREATE TABLE thing (id serial primary key, v text);")
    with connect(blank_db) as c:
        migrate(c, d)
        c.execute("INSERT INTO thing (v) VALUES ('x'), ('y')")
        conftest.truncate_all(c)
        assert count(c, "thing") == 0
        assert count(c) == 2
        assert c.execute("INSERT INTO thing (v) VALUES ('z') RETURNING id").fetchone()[0] == 1


def test_db_fixture_gives_clean_connection(db):
    assert db.execute("SELECT 1").fetchone()[0] == 1
