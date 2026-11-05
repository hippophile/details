"""Rolling notes from Gemma E4B: interval, overlap, quotes, invalid replies, trimming, LM Studio problems."""
import asyncio, json

import pytest

from details.llm import client
from details.llm.client import LLMError
from live_helpers import Msgs, Writer, feed, fake_rec, live_env, script_pcm, wait_until

PHRASE = script_pcm(("t", 1.0), ("s", 0.8))
URL = "http://localhost:1234"


def reply(notes=(), actions=(), tags=()):
    return {"schema_version": 1, "notes": list(notes), "action_items": list(actions), "tags": list(tags)}


def item(text, quote, **kw):
    return {"text": text, "quote": quote, **kw}


class FakeChat:
    """Replaces details.llm.client.chat. Each reply: dict/str, Exception, or async callable(call)."""

    def __init__(self, *replies, default=None):
        self.replies, self.default, self.calls, self.inflight, self.max_inflight = list(replies), default, [], 0, 0

    async def __call__(self, settings, *, model, kind, schema, messages, max_tokens):
        self.calls.append({"model": model, "kind": kind, "schema": schema, "messages": messages})
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            r = self.replies.pop(0) if self.replies else self.default
            if callable(r):
                r = await r(self.calls[-1])
            if isinstance(r, Exception):
                raise r
            text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
            return {"text": text, "tokens_in": 100, "tokens_out": 50, "finish_reason": "stop", "wall_ms": 5, "raw": {}}
        finally:
            self.inflight -= 1


async def start(e, tmp_path, chat, monkeypatch, name="rec1"):
    monkeypatch.setattr(client, "chat", chat)
    live = e.manager.begin(fake_rec(tmp_path, name), -45.0)
    m = Msgs()
    live.attach(m.send)
    await wait_until(lambda: live.state == "listening", what="listening")
    return live, m


async def commit_one(live, m, n=1, w=None):
    w = w or Writer()
    for _ in range(n):
        await feed(live, w, PHRASE)
    await wait_until(lambda: len(m.of("live_commit")) >= n, what=f"{n} commits")
    return w


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


def test_interval_is_honoured_and_exactly_one_call_is_made(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch, DETAILS_LIVE_NOTES_EVERY_MIN="2")
        chat = FakeChat(default=reply([item("Θέμα", "συζητάμε το θέμα 0")]))
        live, m = await start(e, tmp_path, chat, monkeypatch)
        await commit_one(live, m)
        await e.clock.advance(60)
        assert chat.calls == []
        await e.clock.advance(60)
        await wait_until(lambda: m.of("live_notes"), what="notes")
        assert len(chat.calls) == 1
        assert chat.calls[0]["model"] == "details-gemma-cheap" and chat.calls[0]["kind"] == "live_rolling"
        n = m.of("live_notes")[0]
        assert n["seq"] == 1 and n["notes"][0]["verified"] is True and n["verified"] == 1 and n["unverified"] == 0
        await live.close(1.0)
    run(go())


def test_no_new_text_no_call(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        chat = FakeChat(default=reply())
        live, m = await start(e, tmp_path, chat, monkeypatch)
        await e.clock.advance(120)
        await e.clock.advance(120)
        assert chat.calls == [] and e.lm.calls == []              # not even a model load
        await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: len(chat.calls) == 1)
        await e.clock.advance(120)                                # no line since the update
        await asyncio.sleep(0.2)
        assert len(chat.calls) == 1
        await live.close(1.0)
    run(go())


def test_a_running_update_is_not_overlapped(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        gate = asyncio.Event()

        async def slow(call):
            await gate.wait()
            return reply([item("Θέμα", "συζητάμε το θέμα 0")])
        chat = FakeChat(slow, default=reply())
        live, m = await start(e, tmp_path, chat, monkeypatch)
        w = await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: chat.inflight == 1, what="update running")
        await feed(live, w, PHRASE)
        await wait_until(lambda: len(m.of("live_commit")) >= 2)
        await e.clock.advance(120)                                # tick while the update is still running
        await asyncio.sleep(0.2)
        assert len(chat.calls) == 1 and chat.max_inflight == 1
        gate.set()
        await wait_until(lambda: m.of("live_notes"))
        await live.close(1.0)
    run(go())


def test_verified_and_unverified_items_are_both_sent_and_nothing_is_removed(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        chat = FakeChat(reply([item("Θέμα", "συζητάμε το θέμα 0")],
                              [item("Στείλε αναφορά", "θα στείλω την αναφορά αύριο", owner="Νίκος")],
                              [{"name": "θέμα", "quote": "το θέμα 0"}]))
        live, m = await start(e, tmp_path, chat, monkeypatch)
        await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: m.of("live_notes"))
        n = m.of("live_notes")[0]
        assert n["notes"][0]["verified"] is True and n["tags"][0]["verified"] is True
        assert n["action_items"][0]["verified"] is False and n["action_items"][0]["owner"] == "Νίκος"
        assert (n["verified"], n["unverified"]) == (2, 1)
        await live.close(1.0)
    run(go())


def test_two_invalid_replies_keep_the_previous_notes_and_warn(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        good = reply([item("Θέμα", "συζητάμε το θέμα 0")])
        chat = FakeChat(good, '{"nope": 1}', "not json at all")
        live, m = await start(e, tmp_path, chat, monkeypatch)
        w = await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: m.of("live_notes"))
        await feed(live, w, PHRASE)
        await wait_until(lambda: len(m.of("live_commit")) >= 2)
        await e.clock.advance(120)
        await wait_until(lambda: any("update skipped" in x["message"] for x in m.status("notes", "warn")), what="warn")
        assert len(chat.calls) == 3                               # 1 + (invalid, one retry)
        assert len(m.of("live_notes")) == 1 and live.notes["seq"] == 1
        await live.close(1.0)
    run(go())


def test_long_meeting_prompt_has_only_the_newest_text_but_quotes_check_the_whole_transcript(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch, DETAILS_LIVE_MAX_TRANSCRIPT_TOKENS="150", DETAILS_LLM_CHARS_PER_TOKEN="2.5")
        monkeypatch.setenv("LIVE_FAKE_PAD", "40")
        chat = FakeChat(default=reply([item("Αρχικό θέμα", "συζητάμε το θέμα 0")]))
        live, m = await start(e, tmp_path, chat, monkeypatch)
        w = Writer()
        for _ in range(6):
            await feed(live, w, PHRASE)
        await wait_until(lambda: len(m.of("live_commit")) >= 6, what="6 commits")
        newest = live.commits[-1]["text"]
        await e.clock.advance(120)
        await wait_until(lambda: m.of("live_notes"))
        prompt = "\n".join(x["content"] for x in chat.calls[0]["messages"])
        assert newest in prompt
        assert f"συζητάμε το θέμα {live.commits[0]['start_ms']} λέξη" not in prompt      # the oldest line was trimmed
        assert m.of("live_notes")[0]["notes"][0]["verified"] is True                    # ...but is still quotable
        await live.close(1.0)
    run(go())


def test_previous_notes_go_into_the_next_prompt_without_the_verified_flag(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        chat = FakeChat(default=reply([item("Θέμα ένα", "συζητάμε το θέμα 0")]))
        live, m = await start(e, tmp_path, chat, monkeypatch)
        w = await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: m.of("live_notes"))
        await feed(live, w, PHRASE)
        await wait_until(lambda: len(m.of("live_commit")) >= 2)
        await e.clock.advance(120)
        await wait_until(lambda: len(chat.calls) == 2)
        p2 = "\n".join(x["content"] for x in chat.calls[1]["messages"])
        assert "Θέμα ένα" in p2 and '"verified"' not in p2
        assert "verified" not in json.dumps(chat.calls[0]["schema"])
        await live.close(1.0)
    run(go())


def test_lm_studio_down_gives_a_message_with_the_url_and_recovers(tmp_path, monkeypatch):
    async def go():
        from fakes.lmstudio import FakeLMStudio
        lm = FakeLMStudio(reachable=False)
        e = live_env(tmp_path, monkeypatch, lm=lm)
        chat = FakeChat(default=reply([item("Θέμα", "συζητάμε το θέμα 0")]))
        live, m = await start(e, tmp_path, chat, monkeypatch)
        w = await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: m.status("notes", "error"), what="error")
        msg = m.status("notes", "error")[0]["message"]
        assert URL in msg and "Recording and live text continue" in msg
        await feed(live, w, PHRASE)                               # live text keeps appearing
        await wait_until(lambda: len(m.of("live_commit")) >= 2)
        lm.up = True                                              # LM Studio started mid-meeting
        await e.clock.advance(120)
        await wait_until(lambda: m.of("live_notes"), what="recovery")
        assert ("load", "google/gemma-4-e4b", "details-gemma-cheap", 8192) in lm.calls
        await live.close(1.0)
    run(go())


def test_call_timeout_is_a_message_not_a_crash(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch, DETAILS_LIVE_LLM_TIMEOUT_S="0.3")

        async def hang(call):
            await asyncio.sleep(30)
        chat = FakeChat(hang)
        live, m = await start(e, tmp_path, chat, monkeypatch)
        await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: m.status("notes", "error"), what="timeout message")
        assert "0.3" in m.status("notes", "error")[0]["message"] and URL in m.status("notes", "error")[0]["message"]
        assert live.state == "listening"
        await live.close(1.0)
    run(go())


def test_llm_error_from_the_client_is_a_message(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        chat = FakeChat(LLMError(f"cannot reach LM Studio at {URL}: ClientConnectorError", "unreachable"))
        live, m = await start(e, tmp_path, chat, monkeypatch)
        await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: m.status("notes", "error"))
        assert URL in m.status("notes", "error")[0]["message"]
        await live.close(1.0)
    run(go())


def test_not_enough_memory_states_needed_and_free_gb(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch, mem_free=2.0)
        chat = FakeChat(default=reply())
        live, m = await start(e, tmp_path, chat, monkeypatch)
        await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: m.status("notes", "error"))
        msg = m.status("notes", "error")[0]["message"]
        assert "7" in msg and "2" in msg and "GB" in msg and chat.calls == []
        assert not [c for c in e.lm.calls if c[0] == "load"] and live.state == "listening"
        await live.close(1.0)
    run(go())


def test_the_26b_is_never_loaded(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch, DETAILS_LIVE_LLM_MODEL="gemma-nice")
        chat = FakeChat(default=reply())
        live, m = await start(e, tmp_path, chat, monkeypatch)
        assert m.status("notes", "error") or m.status("notes", "warn")
        assert "gemma-nice" in (m.status("notes")[0]["message"])
        await commit_one(live, m)
        await e.clock.advance(120)
        await e.clock.advance(120)
        await asyncio.sleep(0.2)
        assert [c for c in e.lm.calls if c[0] == "load"] == [] and chat.calls == []
        assert live.state == "listening"                          # live text still works
        await live.close(1.0)
    run(go())


def test_the_model_live_loaded_is_unloaded_at_the_end_and_the_users_is_untouched(tmp_path, monkeypatch):
    async def go():
        from fakes.lmstudio import FakeLMStudio
        lm = FakeLMStudio(loaded=[{"identifier": "google/gemma-4-e4b", "modelKey": "google/gemma-4-e4b"}])
        e = live_env(tmp_path, monkeypatch, lm=lm)
        chat = FakeChat(default=reply([item("Θέμα", "συζητάμε το θέμα 0")]))
        live, m = await start(e, tmp_path, chat, monkeypatch)
        await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: m.of("live_notes"))
        assert [x["identifier"] for x in lm.loaded] == ["google/gemma-4-e4b", "details-gemma-cheap"]
        await live.close(1.0)
        await e.manager.close_all()
        assert [x["identifier"] for x in lm.loaded] == ["google/gemma-4-e4b"]
        assert ("unload", "details-gemma-cheap") in lm.calls and ("unload", "google/gemma-4-e4b") not in lm.calls
    run(go())


def test_rolling_files_and_prompt_hash_are_kept(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch)
        chat = FakeChat(default=reply([item("Θέμα", "συζητάμε το θέμα 0")]))
        rec = fake_rec(tmp_path)
        live, m = await start(e, tmp_path, chat, monkeypatch)
        await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: m.of("live_notes"))
        await live.close(1.0)
        d = rec.dir / "live"
        lines = (d / "transcript.jsonl").read_text().splitlines()
        assert json.loads(lines[0])["text"].startswith("συζητάμε")
        r = json.loads((d / "rolling.json").read_text())
        assert r["seq"] == 1 and len(r["prompt_sha256"]) == 64 and r["notes"][0]["text"] == "Θέμα"
    run(go())


def test_no_update_starts_while_paused_and_none_is_forced_on_continue(tmp_path, monkeypatch):
    async def go():
        e = live_env(tmp_path, monkeypatch, DETAILS_LIVE_NOTES_EVERY_MIN="2")
        chat = FakeChat(default=reply([item("Θέμα", "συζητάμε το θέμα 0")]))
        live, m = await start(e, tmp_path, chat, monkeypatch)
        w = await commit_one(live, m)
        await e.clock.advance(120)
        await wait_until(lambda: len(chat.calls) == 1, what="first update")
        await wait_until(lambda: m.of("live_notes"), what="notes")
        live.pause()
        await feed(live, w, PHRASE)                               # more text arrives (a test-side shortcut)
        await wait_until(lambda: len(m.of("live_commit")) >= 2, what="second commit")
        await e.clock.advance(120)
        await e.clock.advance(120)
        assert len(chat.calls) == 1                               # the timer fired twice, nothing started
        live.continue_()
        await asyncio.sleep(0.2)
        assert len(chat.calls) == 1                               # Continue does not force an update
        await e.clock.advance(120)
        await wait_until(lambda: len(chat.calls) == 2, what="update after continue")
        await live.close(1.0)
    run(go())
