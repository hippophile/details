"""speakers-diarization: the pure functions (turn normalising, talk-time numbers, overlap alignment, labelled lines, names)."""
import pytest

from details.services import diarization as d


def t(speaker, a, b):
    return {"speaker": speaker, "start_ms": a, "end_ms": b}


def seg(a, b, text="x"):
    return {"start_ms": a, "end_ms": b, "text": text}


# ---- alignment ----------------------------------------------------------------------------------------------

def test_largest_overlap_wins():
    turns = [t(1, 0, 3000), t(2, 3000, 10000)]
    assert d.align([seg(0, 10000)], turns) == [2]


def test_tie_goes_to_the_lower_speaker_number():
    turns = [t(2, 0, 1000), t(1, 1000, 2000)]
    assert d.align([seg(0, 2000)], turns) == [1]


def test_no_overlap_is_none_never_guessed():
    turns = [t(1, 0, 10000)]
    assert d.align([seg(50000, 52000)], turns) == [None]
    assert d.align([seg(10000, 12000)], turns) == [None]          # touching is not overlapping


def test_one_speaker_per_segment_while_numbers_still_count_both():
    turns = [t(1, 0, 4000), t(2, 4000, 10000)]
    assert d.align([seg(0, 10000)], turns) == [2]
    stats = {s["speaker"]: s for s in d.stats(turns, 1500)}
    assert stats[1]["talk_ms"] == 4000 and stats[2]["talk_ms"] == 6000


def test_align_many_segments_in_order():
    turns = [t(1, 0, 5000), t(2, 5000, 9000), t(1, 9000, 12000)]
    segs = [seg(0, 4000), seg(4500, 8000), seg(8000, 11000), seg(20000, 21000)]
    assert d.align(segs, turns) == [1, 2, 1, None]


def test_align_with_no_turns_is_all_unknown():
    assert d.align([seg(0, 1000), seg(1000, 2000)], []) == [None, None]


# ---- display + render_lines ---------------------------------------------------------------------------------

def test_display_names():
    assert d.display(1, {}) == "Speaker 1"
    assert d.display(2, {2: "Νίκος"}) == "Νίκος"
    assert d.display(None, {2: "Νίκος"}) == "Unknown"


def test_render_lines_name_colon_text_and_unknown():
    segs = [seg(0, 1000, "Γεια"), seg(5000, 6000, "Εντάξει"), seg(9000, 10000, "Χμ")]
    out = d.render_lines(segs, [1, 2, None], {2: "Νίκος"}, 1500)
    assert out == ["Speaker 1: Γεια", "Νίκος: Εντάξει", "Unknown: Χμ"]


def test_render_lines_merges_close_same_speaker_segments_only():
    segs = [seg(0, 1000, "a"), seg(1500, 2500, "b"), seg(4500, 5000, "c"), seg(5100, 5200, "d")]
    out = d.render_lines(segs, [1, 1, 1, 2], {}, 1500)
    assert out == ["Speaker 1: a b", "Speaker 1: c", "Speaker 2: d"]       # gap 500 merges, gap 2000 does not


def test_render_lines_zero_gap_never_merges_with_a_different_speaker():
    segs = [seg(0, 1000, "a"), seg(1000, 2000, "b")]
    assert d.render_lines(segs, [1, 2], {}, 1500) == ["Speaker 1: a", "Speaker 2: b"]


# ---- normalise_turns ----------------------------------------------------------------------------------------

def test_normalise_sorts_clips_clamps_and_renumbers_by_first_appearance():
    raw = [t("SPEAKER_00", 6000, 9000), t("SPEAKER_01", 0, 6000), t("SPEAKER_01", 9000, 10500)]
    got = d.normalise_turns(raw, 10000)
    assert [(x["speaker"], x["start_ms"], x["end_ms"]) for x in got] == [(1, 0, 6000), (2, 6000, 9000), (1, 9000, 10000)]


def test_normalise_overlap_is_clamped_so_no_two_turns_overlap():
    got = d.normalise_turns([t("A", 0, 1000), t("B", 800, 2000)], 5000)
    assert [(x["start_ms"], x["end_ms"]) for x in got] == [(0, 1000), (1000, 2000)]
    for a, b in zip(got, got[1:]):
        assert a["end_ms"] <= b["start_ms"]


def test_normalise_drops_empty_and_fully_covered_turns_and_clips_to_audio():
    raw = [t("A", 0, 10000), t("B", 2000, 5000), t("A", 4000, 4000), t("A", 9800, 10500), t("B", 11000, 12000)]
    got = d.normalise_turns(raw, 10000)
    assert [(x["speaker"], x["start_ms"], x["end_ms"]) for x in got] == [(1, 0, 10000)]


def test_normalise_zero_turns():
    assert d.normalise_turns([], 1000) == []


# ---- stats --------------------------------------------------------------------------------------------------

def test_stats_seventy_thirty_example():
    got = d.stats(d.normalise_turns([t("SPEAKER_01", 0, 6000), t("SPEAKER_00", 6000, 9000),
                                     t("SPEAKER_01", 9000, 10000)], 10000), 1500)
    assert got == [{"speaker": 1, "talk_ms": 7000, "share_pct": 70.0, "turns": 2},
                   {"speaker": 2, "talk_ms": 3000, "share_pct": 30.0, "turns": 1}]


def test_stats_three_equal_speakers_round_to_33_3_and_may_sum_to_99_9():
    got = d.stats([{"speaker": i, "start_ms": (i - 1) * 3000, "end_ms": i * 3000} for i in (1, 2, 3)], 1500)
    assert [s["share_pct"] for s in got] == [33.3, 33.3, 33.3]
    assert round(sum(s["share_pct"] for s in got), 1) == 99.9


def test_stats_close_gaps_merge_in_the_turn_count_not_in_talk_time():
    got = d.stats([{"speaker": 1, "start_ms": 0, "end_ms": 1000}, {"speaker": 1, "start_ms": 1800, "end_ms": 3000}], 1500)
    assert got == [{"speaker": 1, "talk_ms": 2200, "share_pct": 100.0, "turns": 1}]


def test_stats_gap_equal_to_the_limit_does_not_merge():
    got = d.stats([{"speaker": 1, "start_ms": 0, "end_ms": 1000}, {"speaker": 1, "start_ms": 2500, "end_ms": 3000}], 1500)
    assert got[0]["turns"] == 2


def test_stats_zero_turns_is_an_empty_list():
    assert d.stats([], 1500) == []


# ---- names --------------------------------------------------------------------------------------------------

def test_check_name_trims_and_normalises_to_nfc():
    import unicodedata
    nfd = unicodedata.normalize("NFD", "  Νίκος ")
    got = d.check_name(nfd, [])
    assert got == unicodedata.normalize("NFC", "Νίκος") and unicodedata.is_normalized("NFC", got)


@pytest.mark.parametrize("bad", ["", "   ", "x" * 61, "a\nb", "a\rb", "a\x00b", "a\tb", "Νίκος: ο", "a b", None, 5])
def test_check_name_refuses_invalid(bad):
    with pytest.raises(d.NameInvalid):
        d.check_name(bad, [])


def test_check_name_sixty_characters_is_fine():
    assert d.check_name("x" * 60, []) == "x" * 60


def test_check_name_unique_after_nfc_and_casefold_against_other_displays():
    import unicodedata
    others = ["Νίκος", "Speaker 3"]
    for dup in ("νίκος", "ΝΊΚΟΣ", unicodedata.normalize("NFD", "Νίκος"), "speaker 3", "SPEAKER 3"):
        with pytest.raises(d.NameTaken):
            d.check_name(dup, others)
    assert d.check_name("Ελένη", others) == "Ελένη"
