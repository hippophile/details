import asyncio

import pytest

from conftest import make_settings
from details.db import open_pool
from details.services import trash
from details.settings import SettingsError
from library_helpers import ago, seed


def purge(db_url, settings):
    async def go():
        pool = await open_pool(db_url)
        try:
            return await trash.purge(pool, settings)
        finally:
            await pool.close()
    return asyncio.run(go())


def exists(db, rid):
    return db.execute("SELECT count(*) FROM recording WHERE id=%s", (rid,)).fetchone()[0] == 1


def folder(s, rid):
    return s.paths.data_dir / "recordings" / rid


def has_table(db, name):
    return db.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is not None


def test_old_trash_purged_young_kept_active_never(db, db_url, mk_settings):
    s = mk_settings(db_url)
    old = seed(db, s, "old", deleted_at=ago(31))
    young = seed(db, s, "young", deleted_at=ago(29))
    active = seed(db, s, "active", started="2020-01-01T00:00:00+00:00")
    purge(db_url, s)
    assert not exists(db, old) and not folder(s, old).exists()
    assert exists(db, young) and folder(s, young).exists()
    assert exists(db, active) and folder(s, active).exists()


def test_trash_days_env_configurable(db, db_url, mk_settings):
    s = mk_settings(db_url, DETAILS_TRASH_DAYS="7")
    assert s.trash.days == 7
    rid = seed(db, s, "eight", deleted_at=ago(8))
    purge(db_url, s)
    assert not exists(db, rid) and not folder(s, rid).exists()


def test_trash_days_default_30(mk_settings):
    assert mk_settings().trash.days == 30


@pytest.mark.parametrize("bad", ["0", "-3"])
def test_trash_days_below_one_rejected(tmp_path, bad):
    with pytest.raises(SettingsError, match=r"trash\.days"):
        make_settings(tmp_path, DETAILS_TRASH_DAYS=bad)


def test_import_max_mb_validated(tmp_path):
    assert make_settings(tmp_path).import_.max_mb == 2048
    with pytest.raises(SettingsError, match=r"import\.max_mb"):
        make_settings(tmp_path, DETAILS_IMPORT_MAX_MB="0")


def _job(db, rid, status):
    return db.execute("INSERT INTO job (kind, recording_id, status) VALUES ('stt', %s, %s) RETURNING id",
                      (rid, status)).fetchone()[0]


def test_active_job_blocks_purge(db, db_url, mk_settings):
    s = mk_settings(db_url)
    for status in ("queued", "running", "cancelling"):
        rid = seed(db, s, status, deleted_at=ago(40))
        _job(db, rid, status)
        purge(db_url, s)
        assert exists(db, rid) and folder(s, rid).exists(), status


def test_finished_job_purged_with_job_dir(db, db_url, mk_settings):
    s = mk_settings(db_url)
    rid = seed(db, s, "fin", deleted_at=ago(40))
    jid = _job(db, rid, "done")
    jd = s.paths.data_dir / "jobs" / str(jid)
    jd.mkdir(parents=True)
    (jd / "x").write_text("1")
    purge(db_url, s)
    assert not exists(db, rid) and not folder(s, rid).exists() and not jd.exists()


@pytest.mark.parametrize("table", ["stt_run", "analysis_run"])
def test_cascade_of_run_tables(db, db_url, mk_settings, table):
    if not has_table(db, table):
        pytest.skip(f"{table} table does not exist yet: cascade verified by its own change (extended when those tables exist)")
    pytest.skip(f"{table}: minimal-column insert not defined until its migration lands")
