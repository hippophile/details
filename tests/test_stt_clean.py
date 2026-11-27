"""stt-clean-windows: the pure transcript cleaner (junk, repeat loops, end cap, order, overlap trim)."""
from details.stt_clean import JUNK, clean


def seg(a, b, text, **kw):
    return {"start_ms": a, "end_ms": b, "text": text, **kw}


def texts(segs):
    return [s["text"] for s in segs]


def test_junk_phrase_dropped_and_counted():
    out, c = clean([seg(0, 1000, "Καλημέρα"), seg(1000, 2000, "Υπότιτλοι AUTHORWAVE"), seg(2000, 3000, "ok")], 10000)
    assert texts(out) == ["Καλημέρα", "ok"] and c["junk"] == 1


def test_junk_list_is_the_live_worker_list():
    from details.live import worker
    assert worker.JUNK is JUNK


def test_repeat_loop_collapsed_to_one_unit_rest_of_line_kept():
    out, c = clean([seg(0, 5000, "Τότε " + "Αφικού " * 50 + "και τέλος")], 10000)
    assert texts(out) == ["Τότε Αφικού και τέλος"] and c["loops"] == 1


def test_multiword_loop_collapsed():
    out, c = clean([seg(0, 5000, "ναι ναι οκ " * 8)], 10000)
    assert texts(out) == ["ναι ναι οκ"] and c["loops"] == 1


def test_char_loop_collapsed_but_seven_repeats_stay():
    out, c = clean([seg(0, 5000, "Υ" + "i" * 300 + " ok"), seg(5000, 6000, "Υ" + "i" * 7)], 10000)
    assert texts(out) == ["Υi ok", "Υiiiiiii"] and c["loops"] == 1


def test_real_repetition_is_kept():
    segs = [seg(0, 1000, "Gehelfen Gehelfen")] + [seg(1000 + i * 1000, 2000 + i * 1000, "Gehelfen") for i in range(4)]
    out, c = clean(segs, 10000)
    assert texts(out) == texts(segs) and c["loops"] == 0 and c["junk"] == 0


def test_end_capped_at_duration():
    out, c = clean([seg(0, 1000, "a"), seg(9000, 10500, "b")], 10000)
    assert out[1]["end_ms"] == 10000 and c["capped"] == 1


def test_segment_starting_at_or_after_duration_dropped():
    out, c = clean([seg(0, 1000, "a"), seg(10000, 11000, "b"), seg(10500, 11000, "c")], 10000)
    assert texts(out) == ["a"]


def test_out_of_order_sorted():
    out, _ = clean([seg(2000, 3000, "b"), seg(0, 1000, "a")], 10000)
    assert texts(out) == ["a", "b"]


def test_overlap_trimmed_to_next_start():
    out, c = clean([seg(0, 3000, "a"), seg(2000, 4000, "b")], 10000)
    assert out[0]["end_ms"] == 2000 and out[1]["end_ms"] == 4000 and c["trimmed"] == 1


def test_trim_never_makes_zero_length():
    out, _ = clean([seg(1000, 3000, "a"), seg(1000, 2000, "b")], 10000)
    assert out[0]["end_ms"] >= out[0]["start_ms"] + 1


def test_other_fields_kept_and_input_not_mutated():
    src = [seg(0, 20000, "a", avg_logprob=-0.2, words=None)]
    out, _ = clean(src, 10000)
    assert out[0]["avg_logprob"] == -0.2 and src[0]["end_ms"] == 20000


def test_counts_all_keys_present_when_clean():
    out, c = clean([seg(0, 1000, "a")], 10000)
    assert c == {"junk": 0, "loops": 0, "capped": 0, "trimmed": 0, "join_dupes": 0} and texts(out) == ["a"]


def test_five_emphatic_repeats_are_kept_eight_are_a_loop():
    out, c = clean([seg(0, 1000, "No, no, no, no, no!"), seg(2000, 3000, "ok ok ok ok ok ok ok ok")], 10000)
    assert texts(out) == ["No, no, no, no, no!", "ok"] and c["loops"] == 1


# join rule: window_s=30, overlap_s=3, join at 30 s
def join(a, b, **kw):
    return clean([a, b], 100000, window_s=30, overlap_s=3, **kw)


def test_join_duplicate_contained_text_drops_the_shorter():
    out, c = join(seg(28000, 30500, "I was mad."), seg(30000, 33000, "I have to hear it again, because I was mad."))
    assert texts(out) == ["I have to hear it again, because I was mad."] and c["join_dupes"] == 1


def test_join_duplicate_equal_text_keeps_the_earlier():
    out, c = join(seg(28000, 30500, "If there is B"), seg(30000, 32000, "if there is B!"))
    assert out[0]["start_ms"] == 28000 and len(out) == 1 and c["join_dupes"] == 1


def test_join_paraphrase_is_kept():
    out, c = join(seg(28000, 30500, "I don't know what you mean."), seg(30000, 32000, "You know what you mean."))
    assert len(out) == 2 and c["join_dupes"] == 0


def test_same_side_pair_is_kept():
    out, c = clean([seg(10000, 12000, "ναι"), seg(12000, 14000, "ναι ναι")], 100000, window_s=30, overlap_s=3)
    assert len(out) == 2 and c["join_dupes"] == 0


def test_far_from_the_join_is_kept():
    out, c = join(seg(20000, 29000, "ok"), seg(31000, 36000, "ok then"))      # b starts 1 s after, a starts 10 s before
    assert len(out) == 2 and c["join_dupes"] == 0


def test_join_rule_off_without_window_or_overlap():
    a, b = seg(28000, 30500, "I was mad."), seg(30000, 33000, "because I was mad.")
    assert len(clean([a, b], 100000)[0]) == 2
    assert len(clean([a, b], 100000, window_s=30, overlap_s=0)[0]) == 2


def test_one_duplicate_per_pair_a_third_repeat_is_not_chained():
    out, c = clean([seg(59000, 61000, "Γετρύγκτ"), seg(61000, 63000, "Γετρύγκτ"), seg(63000, 65000, "Γετρύγκτ")], 100000,
                   window_s=30, overlap_s=3)
    assert len(out) == 2 and c["join_dupes"] == 1
