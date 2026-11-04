"""run-insights B: structured job log (writer, ordering, restart, cap, what is logged, no prompt text)."""
import asyncio, json, sys

from details.runner.joblog import JobLog, JobLogs
from details.runner.steps import StepRegistry
from fakes.lmstudio import FakeLMStudio
from jobs_helpers import jobs_env, wait_status
from llm_helpers import SEGS, analysis_env, good_responder, seed_run, sync_prompts
from test_llm_steps import start
from test_run_insights_steps import run

JID = "11111111-1111-1111-1111-111111111111"


def test_lines_ordered_across_job_and_steps_with_after(tmp_path):
    lg = JobLog(tmp_path, JID, 100)
    lg.append(None, "info", "runner", "claimed")
    lg.append("a", "info", "step", "a1")
    lg.append("b", "warn", "worker", "b1", {"k": 1})
    lg.append("a", "info", "step", "a2")
    lg.append(None, "info", "runner", "finished")
    lines = lg.read()
    assert [x["n"] for x in lines] == [1, 2, 3, 4, 5]
    assert [x["step"] for x in lines] == [None, "a", "b", "a", None]
    assert [x["src"] for x in lines] == ["runner", "step", "worker", "step", "runner"]
    assert set(lines[0]) == {"n", "ts", "level", "step", "src", "msg", "data"} and lines[2]["data"] == {"k": 1}
    assert (tmp_path / "jobs" / JID / "_job" / "log.jsonl").is_file()
    assert (tmp_path / "jobs" / JID / "a" / "log.jsonl").is_file()
    assert [x["n"] for x in lg.read(after=3)] == [4, 5]
    assert [x["n"] for x in lg.read(limit=2)] == [1, 2]


def test_n_continues_after_restart(tmp_path):
    lg = JobLog(tmp_path, JID, 100)
    for i in range(7):
        lg.append("a" if i % 2 else None, "info", "step", f"m{i}")
    again = JobLog(tmp_path, JID, 100)            # a restarted server: new object, same directory
    assert again.append("a", "info", "step", "after restart")["n"] == 8
    assert [x["n"] for x in again.read()][-2:] == [7, 8]


def test_cap_writes_one_truncation_line_then_stops(tmp_path):
    lg = JobLog(tmp_path, JID, 5)
    for i in range(20):
        lg.append("a", "info", "step", f"line {i}")
    lines = lg.read()
    assert len(lines) == 6 and lines[-1]["msg"] == "log truncated"
    assert [x["msg"] for x in lines[:5]] == [f"line {i}" for i in range(5)]
    assert len(JobLog(tmp_path, JID, 5).read()) == 6        # still capped after a restart
    JobLog(tmp_path, JID, 5).append("a", "info", "step", "more")
    assert len(lg.read()) == 6


def test_long_lines_are_cut(tmp_path):
    lg = JobLog(tmp_path, JID, 5)
    lg.append("a", "info", "step", "x" * 5000)
    assert len(lg.read()[0]["msg"]) <= 2000


def test_runner_logs_steps_job_level_and_ctx_log(db, db_url, mk_settings):
    reg = StepRegistry()

    def step(name):
        async def fn(ctx):
            ctx.log("info", f"{name} first", detail=1)
            ctx.log("warn", f"{name} second")
        reg.register(name)(fn)
    for n in ("one", "two", "three"):
        step(n)

    async def body():
        async with jobs_env(db_url, mk_settings, steps=reg) as e:
            jid = await e.svc.enqueue("test", None, ["one", "two", "three"], {})
            await wait_status(e.svc, jid, "done")
            return jid, JobLogs.for_settings(e.settings).get(jid).read()
    jid, lines = run(body)
    ns = [x["n"] for x in lines]
    assert ns == sorted(ns) and len(set(ns)) == len(ns)
    ours = [(x["step"], x["msg"]) for x in lines if x["src"] == "step"]
    assert ours == [(n, f"{n} {w}") for n in ("one", "two", "three") for w in ("first", "second")]
    runner = [x for x in lines if x["src"] == "runner"]
    assert any(x["step"] is None and "claimed" in x["msg"] for x in runner)
    assert any(x["step"] is None and "finished" in x["msg"] for x in runner)
    for n in ("one", "two", "three"):
        assert any(x["step"] == n and "begin" in x["msg"] for x in runner)
        assert any(x["step"] == n and "done" in x["msg"] and "duration_ms" in x["data"] for x in runner)


def test_lm_load_unload_and_worker_lines_and_stderr_tail(db, db_url, mk_settings):
    code = ("import sys\nprint('hb', flush=True)\n"
            "for i in range(1, 31): print(f'err {i}', file=sys.stderr, flush=True)\nsys.exit(3)")
    reg = StepRegistry()

    async def kid(ctx):
        await ctx.spawn_child([sys.executable, "-c", code])
    reg.register("kid")(kid)

    async def body():
        async with jobs_env(db_url, mk_settings, steps=reg, start=True) as e:
            ok = await e.svc.enqueue("test", None, [{"name": "lm_load", "params": {"model_id": "gemma-cheap"}},
                                                    "lm_unload"], {})
            await wait_status(e.svc, ok, "done")
            bad = await e.svc.enqueue("test", None, ["kid"], {})
            job = await wait_status(e.svc, bad, "failed")
            logs = JobLogs.for_settings(e.settings)
            return logs.get(ok).read(), logs.get(bad).read(), job
    ok_lines, bad_lines, job = run(body)
    mem = [x for x in ok_lines if x["src"] == "memory"]
    assert {x["step"] for x in mem} == {"lm_load", "lm_unload"}
    load = next(x for x in mem if x["step"] == "lm_load")
    assert load["data"]["model"] == "gemma-cheap" and "loaded" in load["data"] and "seconds" in load["data"]
    assert any(x["src"] == "worker" and x["msg"] == "hb" for x in bad_lines)
    tail = [x["msg"] for x in bad_lines if x["msg"].startswith("err ")]
    assert tail == [f"err {i}" for i in range(11, 31)]                        # the last 20 of 30
    assert "exited with code 3" in job["error"]


def test_llm_call_lines_one_per_call_without_text(db, db_url, mk_settings):
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.responder = good_responder

    async def body():
        async with analysis_env(db_url, mk_settings, fake) as e:
            gid, jid = await start(e, run_id, ["debrief", "tags"])
            await wait_status(e.svc, jid, "done")
            return JobLogs.for_settings(e.settings).get(jid).read()
    lines = run(body)
    calls = [x for x in lines if x["src"] == "llm"]
    assert len(calls) == len(fake.requests) == 2
    for x in calls:
        d = x["data"]
        assert d["status"] == "ok" and d["attempt"] == 1 and d["model"].startswith("details-")
        assert d["tokens_in"] > 0 and d["tokens_out"] > 0 and d["finish_reason"] == "stop" and d["wall_ms"] >= 0
        assert d["kind"] in ("debrief", "tags")
    blob = json.dumps(lines, ensure_ascii=False)
    words = {w.strip(".,") for s in SEGS for w in s.split() if len(w) > 4} | {"Ρίσκο", "Σύνοψη", "documentation"}
    assert not [w for w in words if w in blob]


# ---- 1.2 no secrets ---------------------------------------------------------------------------------------
# NOTE: never `assert secret not in text` (a failing assert would print the secret): use leaks().

def leaks(root, secret):
    """-> list of files under root whose text contains the secret."""
    import pathlib
    return [str(p.name) for p in pathlib.Path(root).rglob("*") if p.is_file() and secret in p.read_text(errors="ignore")]


def test_redact_patterns():
    from details.runner.joblog import redact
    s = ("postgresql://u:SeCr3tPw@db/x password=SeCr3tPw&x=1 {\"api_key\": \"SeCr3tPw\"} token: SeCr3tPw "
         "Authorization: Bearer abc.DEF-123 https://user@host/p")
    out = redact(s)
    assert "SeCr3tPw" not in out and "abc.DEF" not in out and "user@" not in out
    assert "postgresql://***@db/x" in out and "x=1" in out
    assert redact("hello world", ["world!!"]) == "hello world"
    assert redact("my secretvalue here", ["secretvalue"]) == "my *** here"
    assert redact("short ab here", ["ab"]) == "short ab here"           # < 6 characters is ignored


def test_secret_in_step_exception_and_bad_db_url(db, db_url, mk_settings):
    from test_job_api import api, poll, post
    reg = StepRegistry()

    async def boom(ctx):
        ctx.log("info", "trying postgresql://u:SeCr3tPw@db/x")
        raise RuntimeError("could not connect to postgresql://u:SeCr3tPw@db/x")

    async def dsn(ctx):
        raise RuntimeError(f"bad url {ctx.settings.db.url}")
    reg.register("boom")(boom)
    reg.register("dsn")(dsn)
    box = {}

    async def body(c, log):
        for name in ("boom", "dsn"):
            jid = (await (await post(c, steps=[name])).json())["id"]
            job = await poll(c, jid, {"failed"})
            box[name] = jid
            texts = [json.dumps(job), await (await c.get(f"/api/jobs/{jid}/log")).text(),
                     await (await c.get(f"/api/jobs/{jid}/log?download=1")).text(),
                     await (await c.get(f"/api/jobs/{jid}/steps")).text()]
            assert (await (await c.get(f"/api/jobs/{jid}/log")).json())["lines"]
            if name == "boom":
                found = any("SeCr3tPw" in t for t in texts)
                assert not found
                assert "db/x" in texts[0] and "could not connect" in texts[0]       # host and the failure survive
            else:
                found = any(str(db_url) in t for t in texts)      # a bool: a failure must not print the DSN
                assert not found
    api(db_url, mk_settings, body, reg=reg)
    # files on disk, too
    import pathlib
    data = mk_settings(db_url).paths.data_dir
    assert not leaks(data / "jobs", "SeCr3tPw")
    assert not leaks(data / "jobs", str(db_url))


def test_secret_in_lm_studio_error_body_reaches_nothing(db, db_url, mk_settings):
    from test_llm_api import api, finish, post
    sync_prompts(db_url)
    _, run_id = seed_run(db, mk_settings(db_url))
    fake = FakeLMStudio()
    fake.script({"status": 500, "error": "upstream failed password=SeCr3tPw for user"})

    async def body(c, s):
        r = await (await post(c, run_id, kinds=["tags"])).json()
        g = await finish(c, r["group_id"])
        jid = r["job_id"]
        assert g["status"] == "failed" and g["error"] and "500" in g["error"]
        texts = [json.dumps(g), await (await c.get(f"/api/jobs/{jid}")).text(),
                 await (await c.get(f"/api/jobs/{jid}/log")).text(),
                 await (await c.get(f"/api/jobs/{jid}/log?download=1")).text(),
                 await (await c.get(f"/api/jobs/{jid}/steps")).text()]
        found = any("SeCr3tPw" in t for t in texts)
        assert not found
        assert not leaks(s.paths.data_dir / "jobs", "SeCr3tPw")
        row = db.execute("SELECT error FROM analysis_run WHERE group_id=%s", (r["group_id"],)).fetchone()
        assert row[0] and "SeCr3tPw" not in row[0]
    api(db_url, mk_settings, body, fake=fake)
