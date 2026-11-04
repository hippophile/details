"""LiveSegmenter: the pure port of v1 live_loop (no asyncio, no model)."""
from details.live.segmenter import Commit, LiveSegmenter, Partial
from live_helpers import SR, script_pcm, silence, tone
from recording_helpers import blocks


def run(seg, pcm, start_frame=0, ms=100):
    out, pos = [], start_frame
    for b in blocks(pcm, ms):
        out += seg.feed(b, pos)
        pos += len(b) // 2
    return out, pos


def commits(events):
    return [e for e in events if isinstance(e, Commit)]


def test_silence_only_sends_nothing():
    seg = LiveSegmenter(-45.0)
    ev, _ = run(seg, silence(10))
    assert ev == [] and seg.flush() == []


def test_commit_on_pause():
    seg = LiveSegmenter(-45.0, partials=False)
    ev, _ = run(seg, script_pcm(("t", 3.0), ("s", 0.8)))
    (c,) = commits(ev)
    assert c.start_ms == 0 and 3000 <= c.end_ms <= 3900
    assert len(c.audio) >= 3 * SR and c.audio.dtype.name == "float32"


def test_long_speech_is_cut_before_16_seconds():
    seg = LiveSegmenter(-45.0, partials=False)
    ev, _ = run(seg, tone(20))
    cs = commits(ev)
    assert cs and cs[0].end_ms < 16000


def test_long_speech_cut_at_the_quietest_100ms_of_the_last_4_seconds():
    seg = LiveSegmenter(-45.0, partials=False)
    pcm = script_pcm(("t", 13.0), ("s", 0.2), ("t", 7.0))        # a 0.2 s dip is too short to count as a pause
    ev, _ = run(seg, pcm)
    assert 13000 <= commits(ev)[0].end_ms <= 13400


def test_partials_every_08s_of_new_speech_and_off_switch():
    on, _ = run(LiveSegmenter(-45.0, partials=True), tone(3))
    ps = [e for e in on if isinstance(e, Partial)]
    assert len(ps) >= 2 and all(p.start_ms == 0 for p in ps)
    assert ps[1].end_ms - ps[0].end_ms >= 700
    off, _ = run(LiveSegmenter(-45.0, partials=False), tone(3))
    assert not [e for e in off if isinstance(e, Partial)]


def test_dropped_chunk_forces_a_boundary():
    seg = LiveSegmenter(-45.0, partials=False)
    ev, pos = run(seg, tone(2))                                   # frames 0..2 s, still speaking
    assert commits(ev) == []
    gap = int(0.5 * SR)
    ev2, _ = run(seg, script_pcm(("t", 2.0), ("s", 0.8)), start_frame=pos + gap)
    cs = commits(ev2)
    assert len(cs) == 2
    assert (cs[0].start_ms, cs[0].end_ms) == (0, 2000)           # committed before the gap
    assert abs(cs[1].start_ms - 2500) <= 1                       # restarted after it
    assert seg.dropped_ms == 500


def test_offsets_follow_the_frame_counter_not_the_chunks_seen():
    seg = LiveSegmenter(-45.0, partials=False)
    run(seg, silence(2))
    ev, _ = run(seg, script_pcm(("t", 1.0), ("s", 0.8)), start_frame=3 * SR)   # 1 s of audio never arrived
    (c,) = commits(ev)
    assert c.start_ms == 3000 and 4000 <= c.end_ms <= 4900
    assert seg.dropped_ms == 1000


def test_flush_commits_pending_speech_and_nothing_else():
    seg = LiveSegmenter(-45.0, partials=False)
    run(seg, tone(2))
    (c,) = seg.flush()
    assert (c.start_ms, c.end_ms) == (0, 2000) and seg.flush() == []
    quiet = LiveSegmenter(-45.0)
    run(quiet, silence(2))
    assert quiet.flush() == []
