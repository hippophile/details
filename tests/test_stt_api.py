"""stt-runs: HTTP API (POST/GET/DELETE) on the real app with the fake engine child."""
import asyncio, json

from conftest import client_for
from details.app import create_app
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from jobs_helpers import wait_until
from stt_helpers import LARGE, TURBO, fake_worker_env, hub_with, insert_run, insert_segments, rec


def api(db_url, mk_settings, body, hub=None, **env):
    async def go():
        s = mk_settings(db_url, DETAILS_STT_PLUGINS="fakes.stt", DETAILS_RUNNER_CANCEL_GRACE_S="1",
                        **({"DETAILS_PATHS_HF_HUB": str(hub)} if hub else {}), **env)
        app = create_app(s, lm_transport=FakeLMStudio(), meminfo=FakeMemInfo())
        async with client_for(app) as c:
            await body(c, s)
    asyncio.run(asyncio.wait_for(go(), 40))


def post(c, rid, **body):
    return c.post(f"/api/recordings/{rid}/stt-runs", json=body)


def test_start_run_202_then_runs_to_done_with_segments(db, db_url, mk_settings, tmp_path, monkeypatch):
    fake_worker_env(monkeypatch, tmp_path)
    s0 = mk_settings(db_url)
    rid = rec(db, s0, seconds=3)
    hub = hub_with(tmp_path, TURBO, LARGE)

    async def body(c, s):
        r = await post(c, rid, model_id="whisper-turbo", language="el", vocab_hint=True)
        assert r.status == 202
        j = await r.json()
        assert j["stt_run_id"] and j["job_id"]
        first = await (await c.get(f"/api/recordings/{rid}/stt-runs")).json()
        assert [x["id"] for x in first] == [j["stt_run_id"]] and first[0]["status"] in ("queued", "running", "done")
        assert first[0]["job_id"] == j["job_id"]

        async def done():
            return (await (await c.get(f"/api/stt-runs/{j['stt_run_id']}")).json())["status"] == "done"
        await wait_until(done, what="run done")
        run = await (await c.get(f"/api/stt-runs/{j['stt_run_id']}")).json()
        assert run["text"].startswith("w0 Τι κάνεις;") and run["audio_ms"] == 3000
        segs = await (await c.get(f"/api/stt-runs/{j['stt_run_id']}/segments")).json()
        assert [x["idx"] for x in segs] == [0, 1, 2] and segs[2]["start_ms"] == 2000 * 1 and "text" in segs[0]
        # a re-run is a new row; the list is newest first
        r2 = await post(c, rid, model_id="whisper-large", language="el")
        assert r2.status == 202
        lst = await (await c.get(f"/api/recordings/{rid}/stt-runs")).json()
        assert [x["id"] for x in lst][1] == j["stt_run_id"] and len(lst) == 2

        async def both():
            return all(x["status"] == "done" for x in await (await c.get(f"/api/recordings/{rid}/stt-runs")).json())
        await wait_until(both, what="both done")
    api(db_url, mk_settings, body, hub, DETAILS_STT_WINDOW_S="1")


def test_unknown_and_llm_model_422_no_row_no_job(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url))

    async def body(c, s):
        for mid in ("nope", "gemma-cheap", None, 5):
            r = await post(c, rid, model_id=mid, language="el")
            assert r.status == 422, mid
        assert (await post(c, rid, model_id="whisper-turbo", language="fr")).status == 422
    api(db_url, mk_settings, body, hub_with(tmp_path, TURBO))
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM job").fetchone()[0] == 0


def test_not_installed_409_with_reason(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url))

    async def body(c, s):
        r = await post(c, rid, model_id="whisper-large", language="el")
        assert r.status == 409 and "whisper-large-v3-mlx" in (await r.json())["error"]
    api(db_url, mk_settings, body, hub_with(tmp_path, TURBO))
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 0


def test_unknown_or_trashed_recording_404(db, db_url, mk_settings, tmp_path):
    async def body(c, s):
        r = await post(c, "00000000-0000-0000-0000-000000000000", model_id="whisper-turbo", language="el")
        assert r.status == 404
        assert (await post(c, "not-a-uuid", model_id="whisper-turbo", language="el")).status == 404
        assert (await c.get("/api/stt-runs/nope")).status == 404
        assert (await c.get("/api/stt-runs/00000000-0000-0000-0000-000000000000/segments")).status == 404
    api(db_url, mk_settings, body, hub_with(tmp_path, TURBO))


def test_delete_queued_or_running_409_done_ok(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url))
    q = insert_run(db, rid, status="queued")
    r_ = insert_run(db, rid, status="running")
    d = insert_run(db, rid, status="done")
    insert_segments(db, d, 3)

    async def body(c, s):
        for x in (q, r_):
            assert (await c.delete(f"/api/stt-runs/{x}")).status == 409
        assert (await c.delete(f"/api/stt-runs/{d}")).status == 200
        assert (await c.get(f"/api/stt-runs/{d}")).status == 404
    api(db_url, mk_settings, body)
    assert db.execute("SELECT count(*) FROM stt_run").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM transcript_segment").fetchone()[0] == 0


def test_list_returns_label_wall_and_rtf(db, db_url, mk_settings):
    rid = rec(db, mk_settings(db_url))
    insert_run(db, rid, model_id="whisper-large", wall_ms=9700, audio_ms=77000, rtf=77 / 9.7, language="el")
    insert_run(db, rid, model_id="unknown (v1 import)", text="old")

    async def body(c, s):
        lst = await (await c.get(f"/api/recordings/{rid}/stt-runs")).json()
        big = next(x for x in lst if x["model_id"] == "whisper-large")
        assert big["wall_ms"] == 9700 and abs(big["rtf"] - 7.9) < 0.05 and big["model_label"] == "Whisper large-v3"
        for part in ("Whisper large-v3", "9.7 s", "7.9×"):
            assert part in big["label"]
        v1 = next(x for x in lst if x["model_id"] == "unknown (v1 import)")
        assert v1["model_label"] == "unknown (v1 import)" and "text" not in v1     # list stays light
    api(db_url, mk_settings, body)


def test_list_adopts_a_v1_transcript_lazily(db, db_url, mk_settings):
    s0 = mk_settings(db_url)
    rid = rec(db, s0)
    (s0.paths.data_dir / "recordings" / rid / "v1-transcript.txt").write_text("παλιό", encoding="utf-8")

    async def body(c, s):
        lst = await (await c.get(f"/api/recordings/{rid}/stt-runs")).json()
        assert [x["model_id"] for x in lst] == ["unknown (v1 import)"]
        run = await (await c.get(f"/api/stt-runs/{lst[0]['id']}")).json()
        assert run["text"] == "παλιό"
        assert (await (await c.get(f"/api/stt-runs/{lst[0]['id']}/segments")).json()) == []
    api(db_url, mk_settings, body)


def test_startup_adopts_v1_transcripts_and_library_counts_them(db, db_url, mk_settings):
    s0 = mk_settings(db_url)
    rid = rec(db, s0, title="with")
    rec(db, s0, title="without")
    (s0.paths.data_dir / "recordings" / rid / "v1-transcript.txt").write_text("x")

    async def body(c, s):
        lib = await (await c.get("/api/recordings")).json()
        assert {r["title"]: r["runs_count"] for r in lib} == {"with": 1, "without": 0}
    api(db_url, mk_settings, body)
