"""llm-outputs: chunking at segment boundaries, overlap, disk cache keys."""
from details.llm import chunking


def segs(n, size=40):
    return [f"{i:02d}" + "x" * (size - 2) for i in range(n)]


def test_estimate_is_chars_over_chars_per_token():
    assert chunking.estimate_tokens("a" * 100, 2.5) == 40


def test_chunks_are_whole_segments_covering_everything_in_order():
    s = segs(6)
    chunks = chunking.split_segments(s, chunk_tokens=100, overlap_tokens=0, chars_per_token=1)
    assert len(chunks) == 3 and all(len(c) == 2 for c in chunks)
    assert [x for c in chunks for x in c] == s


def test_overlap_repeats_trailing_segments():
    s = segs(6)
    chunks = chunking.split_segments(s, chunk_tokens=100, overlap_tokens=40, chars_per_token=1)
    assert chunks[0] == s[0:2] and chunks[1][0] == s[1]       # one whole segment carried over
    assert chunks[-1][-1] == s[-1]
    assert all(set(c) <= set(s) for c in chunks)


def test_one_oversized_segment_is_its_own_chunk_and_progress_is_guaranteed():
    s = ["a" * 500, "b" * 10, "c" * 10]
    chunks = chunking.split_segments(s, chunk_tokens=100, overlap_tokens=50, chars_per_token=1)
    assert chunks[0] == [s[0]] and [x for c in chunks for x in c if x in s[1:]][-1] == s[2]
    assert len(chunks) <= 4


def test_short_transcript_is_one_chunk():
    s = segs(3)
    assert chunking.split_segments(s, 10_000, 200, 2.5) == [s]


def test_cache_key_depends_on_every_input():
    base = ("tags", "sha", "model", {"t": 0.1}, "text")
    k = chunking.cache_key(*base)
    assert k == chunking.cache_key(*base) and len(k) == 64
    for i, other in enumerate(("debrief", "sha2", "model2", {"t": 0.2}, "text2")):
        args = list(base)
        args[i] = other
        assert chunking.cache_key(*args) != k


def test_cache_roundtrip_is_atomic_and_miss_returns_none(tmp_path):
    assert chunking.cache_get(tmp_path, "ab" * 32) is None
    chunking.cache_put(tmp_path, "ab" * 32, {"content": {"a": "Ελληνικά"}})
    assert chunking.cache_get(tmp_path, "ab" * 32) == {"content": {"a": "Ελληνικά"}}
    assert (tmp_path / "cache" / "llm" / ("ab" * 32 + ".json")).exists()
    assert not list((tmp_path / "cache" / "llm").glob("*.tmp"))
