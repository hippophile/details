"""llm-streaming: the in-memory per-job stream hub."""
import asyncio

from details.llm.stream_hub import StreamHub

J = "job-1"


def test_snapshot_holds_the_current_attempt_text():
    h = StreamHub()
    h.start_attempt(J, "checklist", 1)
    h.delta(J, "abc")
    h.delta(J, "def", 2)
    s = h.snapshot(J)
    assert s["kind"] == "checklist" and s["attempt"] == 1 and s["chunk"] is None
    assert s["text"] == "abcdef" and s["pieces"] == 3 and s["active"] is True and s["truncated"] is False


def test_retry_resets_the_text_and_numbers_the_attempt():
    h = StreamHub()
    h.start_attempt(J, "tags", 1)
    h.delta(J, "bad json")
    h.end_attempt(J, valid=False, finish_reason="stop", tokens_out=3)
    s = h.snapshot(J)
    assert s["text"] == "bad json" and s["active"] is False and s["valid"] is False   # kept until the next attempt
    h.start_attempt(J, "tags", 2)
    s = h.snapshot(J)
    assert s["attempt"] == 2 and s["text"] == "" and s["pieces"] == 0 and s["active"] is True


def test_map_reduce_chunk_is_part_of_the_attempt():
    h = StreamHub()
    h.start_attempt(J, "notes", 1, chunk=(2, 5))
    assert h.snapshot(J)["chunk"] == [2, 5]


def test_cap_keeps_the_tail_and_flags_truncation():
    h = StreamHub(cap=100)
    h.start_attempt(J, "tags", 1)
    for i in range(25):
        h.delta(J, f"{i:09d}|")                       # 10 chars each, 250 in total
    s = h.snapshot(J)
    assert len(s["text"]) <= 100 and s["truncated"] is True and s["text"].endswith("000000024|")
    assert s["pieces"] == 25


def test_events_have_increasing_ids_and_can_be_resumed():
    h = StreamHub()
    h.start_attempt(J, "tags", 1)
    h.delta(J, "a")
    mid = h.snapshot(J)["seq"]
    h.delta(J, "b")
    h.delta(J, "c")
    ev = h.events_since(J, mid)
    assert [(e[1], e[2]["text"]) for e in ev] == [("delta", "b"), ("delta", "c")]
    assert [e[0] for e in ev] == sorted(e[0] for e in ev) and ev[0][0] > mid
    assert h.events_since(J, h.snapshot(J)["seq"]) == []
    h.start_attempt(J, "tags", 2)                     # a new attempt does not break resuming: the page sees attempt_start
    assert [e[1] for e in h.events_since(J, mid)] == ["delta", "delta", "attempt_start"]


def test_resume_is_refused_when_the_id_fell_out_of_the_ring():
    h = StreamHub(max_events=5)
    h.start_attempt(J, "tags", 1)
    for i in range(10):
        h.delta(J, str(i))
    assert h.events_since(J, 1) is None and len(h.events_since(J, 8)) == 3


def test_two_subscribers_read_the_same_events():
    h = StreamHub()
    h.subscribe(J)
    h.subscribe(J)
    h.start_attempt(J, "tags", 1)
    h.delta(J, "x")
    assert h.events_since(J, 0) == h.events_since(J, 0) and len(h.events_since(J, 0)) == 2


def test_wait_wakes_on_news_and_times_out_without():
    async def go():
        h = StreamHub()
        h.start_attempt(J, "tags", 1)
        seq = h.snapshot(J)["seq"]
        assert await h.wait(J, seq, 0.1) is False
        asyncio.get_running_loop().call_later(0.05, h.delta, J, "z")
        assert await h.wait(J, seq, 2) is True
    asyncio.run(asyncio.wait_for(go(), 10))


def test_end_job_drops_the_buffer_and_wakes_waiters():
    async def go():
        h = StreamHub()
        h.start_attempt(J, "tags", 1)
        h.delta(J, "secret-ish reply")
        seq = h.snapshot(J)["seq"]
        asyncio.get_running_loop().call_later(0.05, h.end_job, J)
        assert await h.wait(J, seq, 2) is True
        s = h.snapshot(J)
        assert s["text"] == "" and s["active"] is False
    asyncio.run(asyncio.wait_for(go(), 10))


def test_jobs_are_isolated_and_unknown_jobs_snapshot_empty():
    h = StreamHub()
    h.start_attempt("a", "tags", 1)
    h.delta("a", "only a")
    assert h.snapshot("b")["text"] == "" and h.snapshot("a")["text"] == "only a"


def test_release_forgets_an_idle_job():
    h = StreamHub()
    h.subscribe(J)
    h.release(J)
    assert h.snapshot(J)["text"] == "" and h.snapshot(J)["seq"] == 0
