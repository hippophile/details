"""speakers-diarization: HTTP API (runs, turns, rename, segments with speakers) on the real app with the fake engine."""
import asyncio, hashlib, json, unicodedata

from conftest import client_for
from details.app import create_app
from diar_helpers import PLUGIN, SENTINEL, SEGMENTS, diar_hub, diar_ready, fake_diar_env, insert_diar_run, seed_transcript
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from jobs_helpers import wait_until
from stt_helpers import insert_run, rec


def api(db_url, mk_settings, body, hub, **env):
    async def go():
        s = mk_settings(db_url, DETAILS_RUNNER_CANCEL_GRACE_S="1", DETAILS_PATHS_HF_HUB=str(hub), **PLUGIN, **env)
        app = create_app(s, lm_transport=FakeLMStudio(), meminfo=FakeMemInfo())
        async with client_for(app) as c:
            await body(c, s)
    asyncio.run(asyncio.wait_for(go(), 40))


def runs_url(rid):
    return f"/api/recordings/{rid}/diarization-runs"


def counts(db):
    return (db.execute("SELECT count(*) FROM diarization_run").fetchone()[0], db.execute("SELECT count(*) FROM job").fetchone()[0])


def test_start_run_202_runs_to_done_with_hints_turns_and_job_id(db, db_url, mk_settings, tmp_path, monkeypatch):
    diar_ready(monkeypatch)
    fake_diar_env(monkeypatch, tmp_path)
    rid = rec(db, mk_settings(db_url), seconds=66)

    async def body(c, s):
        r = await c.post(runs_url(rid), json={"min_speakers": 2, "max_speakers": 3})
        assert r.status == 202
        j = await r.json()
        assert j["diarization_run_id"] and j["job_id"]
        first = await (await c.get(runs_url(rid))).json()
        assert [x["id"] for x in first] == [j["diarization_run_id"]] and first[0]["job_id"] == j["job_id"]
        assert first[0]["settings"]["min_speakers"] == 2 and first[0]["settings"]["max_speakers"] == 3
        assert first[0]["status"] in ("queued", "running", "done")

        async def done():
            return (await (await c.get(f"/api/diarization-runs/{j['diarization_run_id']}")).json())["status"] == "done"
        await wait_until(done, what="run done")
        run = await (await c.get(f"/api/diarization-runs/{j['diarization_run_id']}")).json()
        assert [x["speaker"] for x in run["speakers"]] == [1, 2, 3] and run["audio_ms"] == 66000
        t = await (await c.get(f"/api/diarization-runs/{j['diarization_run_id']}/turns")).json()
        assert [(x["start_ms"], x["end_ms"], x["speaker"]) for x in t] == [(0, 20000, 1), (20000, 45000, 2), (45000, 66000, 3)]
        r2 = await c.post(runs_url(rid), json={"model_id": "community-1"})
        assert r2.status == 202                                                # a re-run is a new row
        assert len(await (await c.get(runs_url(rid))).json()) == 2
        await wait_until(lambda: counts(db)[0] == 2, what="second row")

        async def both():
            return all(x["status"] == "done" for x in await (await c.get(runs_url(rid))).json())
        await wait_until(both, what="both done")
    api(db_url, mk_settings, body, diar_hub(tmp_path))


def test_unavailable_model_409_with_reason_and_hint_nothing_created(db, db_url, mk_settings, tmp_path, monkeypatch):
    diar_ready(monkeypatch)
    rid = rec(db, mk_settings(db_url))

    async def body(c, s):
        r = await c.post(runs_url(rid), json={})
        j = await r.json()
        assert r.status == 409 and "not downloaded" in j["error"] and "make diar-model" in j["hint"]
    api(db_url, mk_settings, body, diar_hub(tmp_path, "missing"))
    assert counts(db) == (0, 0)


def test_package_missing_409_names_make_setup_diar(db, db_url, mk_settings, tmp_path, monkeypatch):
    from details.services import models
    monkeypatch.setattr(models, "pyannote_importable", lambda: False)
    rid = rec(db, mk_settings(db_url))

    async def body(c, s):
        r = await c.post(runs_url(rid), json={})
        assert r.status == 409 and "make setup-diar" in (await r.json())["hint"]
    api(db_url, mk_settings, body, diar_hub(tmp_path))
    assert counts(db) == (0, 0)


def test_bad_model_ids_422_nothing_created(db, db_url, mk_settings, tmp_path, monkeypatch):
    diar_ready(monkeypatch)
    rid = rec(db, mk_settings(db_url))

    async def body(c, s):
        for mid in ("nope", "whisper-turbo", "gemma-cheap", 5, ""):
            r = await c.post(runs_url(rid), json={"model_id": mid})
            assert r.status == 422, mid
        assert (await c.post(runs_url(rid), data=b"not json", headers={"Content-Type": "application/json"})).status == 422
        assert (await c.post(runs_url(rid), json=[1])).status == 422
    api(db_url, mk_settings, body, diar_hub(tmp_path))
    assert counts(db) == (0, 0)


def test_bad_speaker_hints_422_nothing_created(db, db_url, mk_settings, tmp_path, monkeypatch):
    diar_ready(monkeypatch)
    rid = rec(db, mk_settings(db_url))

    async def body(c, s):
        for bad in ({"min_speakers": 4, "max_speakers": 2}, {"min_speakers": 0}, {"max_speakers": 11},
                    {"min_speakers": "two"}, {"max_speakers": True}, {"min_speakers": 1.5}, {"max_speakers": -1}):
            assert (await c.post(runs_url(rid), json=bad)).status == 422, bad
    api(db_url, mk_settings, body, diar_hub(tmp_path))
    assert counts(db) == (0, 0)


def test_hint_bounds_are_accepted(db, db_url, mk_settings, tmp_path, monkeypatch):
    diar_ready(monkeypatch)
    fake_diar_env(monkeypatch, tmp_path)
    rid = rec(db, mk_settings(db_url))

    async def body(c, s):
        for ok in ({"min_speakers": 1, "max_speakers": 10}, {"max_speakers": 3}, {"min_speakers": 2, "max_speakers": 2}):
            assert (await c.post(runs_url(rid), json=ok)).status == 202, ok
    api(db_url, mk_settings, body, diar_hub(tmp_path))


def test_unknown_malformed_and_trashed_recording_404(db, db_url, mk_settings, tmp_path, monkeypatch):
    diar_ready(monkeypatch)
    rid = rec(db, mk_settings(db_url))
    db.execute("UPDATE recording SET deleted_at=now() WHERE id=%s", (rid,))

    async def body(c, s):
        for r in (rid, "00000000-0000-0000-0000-000000000000", "nope"):
            assert (await c.post(runs_url(r), json={})).status == 404, r
        assert (await c.get(runs_url("00000000-0000-0000-0000-000000000000"))).status == 404
        assert (await c.get("/api/diarization-runs/nope")).status == 404
        assert (await c.get("/api/diarization-runs/00000000-0000-0000-0000-000000000000/turns")).status == 404
    api(db_url, mk_settings, body, diar_hub(tmp_path))
    assert counts(db) == (0, 0)


def test_run_detail_lists_speakers_with_names_and_display(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url))
    dr = insert_diar_run(db, rid, names={2: "Νίκος"})

    async def body(c, s):
        run = await (await c.get(f"/api/diarization-runs/{dr}")).json()
        sp = {x["speaker"]: x for x in run["speakers"]}
        assert set(sp[1]) == {"speaker", "name", "display", "talk_ms", "share_pct", "turns"}
        assert sp[1]["display"] == "Speaker 1" and sp[1]["name"] is None
        assert sp[2]["display"] == "Νίκος" and sp[2]["name"] == "Νίκος" and sp[2]["talk_ms"] == 25000
        lst = await (await c.get(runs_url(rid))).json()
        assert {x["speaker"]: x["display"] for x in lst[0]["speakers"]}[2] == "Νίκος"
    api(db_url, mk_settings, body, diar_hub(tmp_path))


def test_list_label_has_number_model_wall_and_speaker_count(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url))
    insert_diar_run(db, rid)
    insert_diar_run(db, rid)
    third = insert_diar_run(db, rid)

    async def body(c, s):
        lst = await (await c.get(runs_url(rid))).json()
        assert [x["id"] for x in lst][0] == third                             # newest first
        label = lst[0]["label"]
        assert "#3" in label and "pyannote community-1" in label and "3:12" in label and "3 speakers" in label
        assert lst[0]["model_label"] == "pyannote community-1"
    api(db_url, mk_settings, body, diar_hub(tmp_path))


def test_label_of_a_run_that_is_not_done_shows_its_status(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url))
    insert_diar_run(db, rid, status="failed", error="boom")

    async def body(c, s):
        lst = await (await c.get(runs_url(rid))).json()
        assert lst[0]["label"].endswith("failed") and lst[0]["error"] == "boom"
    api(db_url, mk_settings, body, diar_hub(tmp_path))


def test_delete_busy_409_done_200_unknown_404_and_turns_go_with_it(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url))
    q, r_, d = (insert_diar_run(db, rid, status=s) for s in ("queued", "running", "done"))

    async def body(c, s):
        for busy in (q, r_):
            assert (await c.delete(f"/api/diarization-runs/{busy}")).status == 409
        assert db.execute("SELECT count(*) FROM diarization_run").fetchone()[0] == 3
        assert (await c.delete(f"/api/diarization-runs/{d}")).status == 200
        assert (await c.delete(f"/api/diarization-runs/{d}")).status == 404
    api(db_url, mk_settings, body, diar_hub(tmp_path))
    assert db.execute("SELECT count(*) FROM diarization_run").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM diar_turn").fetchone()[0] == 0


# ---- rename -------------------------------------------------------------------------------------------------

def patch(c, dr, n, name, **kw):
    return c.patch(f"/api/diarization-runs/{dr}/speakers/{n}", json={"name": name}, **kw)


def hash_transcripts(db):
    h = hashlib.sha256()
    for r in db.execute("SELECT text FROM transcript_segment ORDER BY stt_run_id, idx"):
        h.update(r[0].encode())
    for r in db.execute("SELECT text FROM stt_run ORDER BY id"):
        h.update((r[0] or "").encode())
    return h.hexdigest()


def test_rename_set_then_reset_and_transcripts_are_untouched(db, db_url, mk_settings, tmp_path):
    rid, _ = seed_transcript(db, mk_settings(db_url))
    dr = insert_diar_run(db, rid)
    before = hash_transcripts(db)
    turns_before = db.execute("SELECT idx, start_ms, end_ms, speaker FROM diar_turn ORDER BY idx").fetchall()

    async def body(c, s):
        async def display(n):
            run = await (await c.get(f"/api/diarization-runs/{dr}")).json()
            return {x["speaker"]: x["display"] for x in run["speakers"]}[n]
        r = await patch(c, dr, 1, "Ελένη")
        assert r.status == 200 and await display(1) == "Ελένη"
        r = await patch(c, dr, 1, None)
        assert r.status == 200 and await display(1) == "Speaker 1"
        assert (await patch(c, dr, 1, "  Ελένη  ")).status == 200 and await display(1) == "Ελένη"
        nfd = unicodedata.normalize("NFD", "Άννα")
        assert (await patch(c, dr, 3, nfd)).status == 200
        assert await display(3) == unicodedata.normalize("NFC", nfd)
    api(db_url, mk_settings, body, diar_hub(tmp_path))
    assert hash_transcripts(db) == before
    assert db.execute("SELECT idx, start_ms, end_ms, speaker FROM diar_turn ORDER BY idx").fetchall() == turns_before


def test_rename_duplicates_are_409_and_leave_the_name_unchanged(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url))
    dr = insert_diar_run(db, rid, names={2: "Νίκος"})

    async def body(c, s):
        for dup in ("νίκος", "ΝΊΚΟΣ", unicodedata.normalize("NFD", "Νίκος"), "Speaker 2", "speaker 2"):
            r = await patch(c, dr, 1, dup)
            assert r.status == 409, dup
        assert db.execute("SELECT count(*) FROM speaker_name WHERE speaker=1").fetchone()[0] == 0
        assert (await patch(c, dr, 1, "Speaker 1")).status == 200            # its own default is fine
        assert (await patch(c, dr, 2, "νίκος")).status == 200                # a name may be re-cased by its owner
    api(db_url, mk_settings, body, diar_hub(tmp_path))


def test_rename_invalid_names_422(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url))
    dr = insert_diar_run(db, rid)

    async def body(c, s):
        for bad in ("", "   ", "x" * 61, "a\nb", "Νίκος: ο", 5, ["x"]):
            assert (await patch(c, dr, 1, bad)).status == 422, bad
        assert (await c.patch(f"/api/diarization-runs/{dr}/speakers/1", json={"nom": "x"})).status == 422
        assert (await c.patch(f"/api/diarization-runs/{dr}/speakers/1", json=[1])).status == 422
        assert (await patch(c, dr, 1, "x" * 60)).status == 200
    api(db_url, mk_settings, body, diar_hub(tmp_path))
    assert db.execute("SELECT name FROM speaker_name WHERE speaker=1").fetchone() == ("x" * 60,)


def test_rename_unknown_speaker_or_run_404(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url))
    dr = insert_diar_run(db, rid)

    async def body(c, s):
        assert (await patch(c, dr, 4, "x")).status == 404
        assert (await patch(c, dr, 0, "x")).status == 404
        assert (await c.patch(f"/api/diarization-runs/{dr}/speakers/abc", json={"name": "x"})).status == 404
        assert (await patch(c, "00000000-0000-0000-0000-000000000000", 1, "x")).status == 404
    api(db_url, mk_settings, body, diar_hub(tmp_path))


def test_cross_origin_rename_is_refused(db, db_url, mk_settings, tmp_path):
    rid = rec(db, mk_settings(db_url))
    dr = insert_diar_run(db, rid)

    async def body(c, s):
        r = await patch(c, dr, 1, "Evil", headers={"Origin": "http://evil.example"})
        assert r.status == 403
        r = await c.post(runs_url(rid), json={}, headers={"Origin": "http://evil.example"})
        assert r.status == 403
        assert (await c.delete(f"/api/diarization-runs/{dr}", headers={"Origin": "http://evil.example"})).status == 403
    api(db_url, mk_settings, body, diar_hub(tmp_path))
    assert db.execute("SELECT count(*) FROM speaker_name").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM diarization_run").fetchone()[0] == 1


# ---- segments with speakers ---------------------------------------------------------------------------------

def test_segments_with_a_diarization_run_get_speaker_fields_by_overlap(db, db_url, mk_settings, tmp_path):
    rid, run_id = seed_transcript(db, mk_settings(db_url))
    dr = insert_diar_run(db, rid, names={2: "Νίκος"})

    async def body(c, s):
        segs = await (await c.get(f"/api/stt-runs/{run_id}/segments?diarization_run_id={dr}")).json()
        assert [x["speaker"] for x in segs] == [1, 1, 2, 2, 3, 3, 3]
        assert [x["speaker_name"] for x in segs] == ["Speaker 1", "Speaker 1", "Νίκος", "Νίκος", "Speaker 3", "Speaker 3", "Speaker 3"]
        assert {"idx", "start_ms", "end_ms", "text", "avg_logprob", "no_speech_prob", "speaker", "speaker_name"} == set(segs[0])
    api(db_url, mk_settings, body, diar_hub(tmp_path))


def test_segment_without_overlap_is_unknown(db, db_url, mk_settings, tmp_path):
    rid, run_id = seed_transcript(db, mk_settings(db_url))
    dr = insert_diar_run(db, rid, turns=[("A", 0, 5000)])

    async def body(c, s):
        segs = await (await c.get(f"/api/stt-runs/{run_id}/segments?diarization_run_id={dr}")).json()
        assert segs[0]["speaker"] == 1 and segs[1]["speaker"] is None and segs[1]["speaker_name"] == "Unknown"
    api(db_url, mk_settings, body, diar_hub(tmp_path))


def test_segments_without_the_parameter_keep_exactly_todays_keys(db, db_url, mk_settings, tmp_path):
    rid, run_id = seed_transcript(db, mk_settings(db_url))
    insert_diar_run(db, rid)

    async def body(c, s):
        segs = await (await c.get(f"/api/stt-runs/{run_id}/segments")).json()
        assert len(segs) == 7 and set(segs[0]) == {"idx", "start_ms", "end_ms", "text", "avg_logprob", "no_speech_prob"}
    api(db_url, mk_settings, body, diar_hub(tmp_path))


def test_segments_422_for_other_recording_not_done_unknown_or_malformed(db, db_url, mk_settings, tmp_path):
    rid, run_id = seed_transcript(db, mk_settings(db_url))
    other = rec(db, mk_settings(db_url))
    foreign = insert_diar_run(db, other)
    running = insert_diar_run(db, rid, status="running")

    async def body(c, s):
        for bad in (foreign, running, "00000000-0000-0000-0000-000000000000", "nope"):
            r = await c.get(f"/api/stt-runs/{run_id}/segments?diarization_run_id={bad}")
            assert r.status == 422, bad
        assert (await c.get("/api/stt-runs/00000000-0000-0000-0000-000000000000/segments")).status == 404
    api(db_url, mk_settings, body, diar_hub(tmp_path))


# ---- secrets ------------------------------------------------------------------------------------------------

def test_token_sentinel_is_in_no_response_body(db, db_url, mk_settings, tmp_path, monkeypatch):
    diar_ready(monkeypatch)
    fake_diar_env(monkeypatch, tmp_path)
    rid, run_id = seed_transcript(db, mk_settings(db_url))
    done = insert_diar_run(db, rid, names={1: "Ελένη"})
    bodies = []

    async def body(c, s):
        assert s.diarization.hf_token == SENTINEL
        r = await c.post(runs_url(rid), json={"min_speakers": 2})
        bodies.append(await r.text())
        j = json.loads(bodies[-1])
        for path in ("/api/models", "/api/models?kind=diar", runs_url(rid), f"/api/diarization-runs/{done}",
                     f"/api/diarization-runs/{done}/turns", f"/api/stt-runs/{run_id}/segments?diarization_run_id={done}",
                     f"/api/jobs/{j['job_id']}", f"/api/jobs/{j['job_id']}/log", f"/api/jobs/{j['job_id']}/steps"):
            bodies.append(await (await c.get(path)).text())

        async def finished():
            return (await (await c.get(f"/api/diarization-runs/{j['diarization_run_id']}")).json())["status"] == "done"
        await wait_until(finished, what="run done")
        bodies.append(await (await c.get(f"/api/diarization-runs/{j['diarization_run_id']}")).text())
        bodies.append(await (await c.get(f"/api/jobs/{j['job_id']}/log")).text())
    api(db_url, mk_settings, body, diar_hub(tmp_path), HF_TOKEN=SENTINEL)
    assert len(bodies) > 8 and not [b for b in bodies if SENTINEL in b]
