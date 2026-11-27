"""llm-outputs: verbatim quote verification (same norm() as eval/score.py)."""
import importlib.util

from details.llm import quotes
from details.settings import REPO_ROOT

FULL = "Καλημέρα. Θα κάνουμε deploy στις 12 Νοεμβρίου, ΟΚ; Το πλάνο είναι σίγουρο."


def test_norm_is_the_eval_score_norm():
    spec = importlib.util.spec_from_file_location("score_ref", REPO_ROOT / "eval" / "score.py")
    ref = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)
    for t in ("Θα κάνουμε ΟΚ", "σίγουρος ς", "3.000 ευρώ 70%"):
        assert quotes.norm(t) == ref.norm(t)


def item(q):
    return {"text": "t", "quote": q}


def test_case_tonos_and_final_sigma_do_not_matter():
    c = {"decisions": [item("θα κανουμε deploy στισ 12"), item("ΣΊΓΟΥΡΟ")]}
    out, v, u = quotes.verify(c, FULL)
    assert [i["verified"] for i in out["decisions"]] == [True, True] and (v, u) == (2, 0)


def test_invented_quote_is_kept_and_marked_unverified():
    c = {"items": [item("Θα κάνουμε deploy"), item("Θα απολύσουμε τον Νίκο")]}
    out, v, u = quotes.verify(c, FULL)
    assert [i["verified"] for i in out["items"]] == [True, False]
    assert len(out["items"]) == 2 and (v, u) == (1, 1)


def test_empty_quote_is_never_verified_and_input_not_mutated():
    c = {"tags": [item(""), item("   ")]}
    out, v, u = quotes.verify(c, FULL)
    assert [i["verified"] for i in out["tags"]] == [False, False]
    assert "verified" not in c["tags"][0]


def test_nested_sections_and_prose_fields_are_handled():
    c = {"title": "no quote here", "sections": [{"heading": "h", "bullets": [item("το πλάνο είναι")]}]}
    out, v, u = quotes.verify(c, FULL)
    assert out["sections"][0]["bullets"][0]["verified"] is True and "verified" not in out["sections"][0]
    assert (v, u) == (1, 0)


SEGS = [(0, "Καλημέρα σε όλους."), (5000, "Θα κάνουμε deploy"), (9000, "..."), (12000, "στις 12 Νοεμβρίου. Ωραία!"),
        (20000, "Ωραία! Τέλος.")]


def test_locate_sets_at_ms_to_the_start_of_the_segment_where_the_quote_begins():
    c = {"decisions": [item("ΚΑΛΗΜΕΡΑ σε ολουσ"), item("deploy στις 12 νοεμβριου")], "nested": {"b": [item("τελοσ")]}}
    out = quotes.locate(c, SEGS)
    assert [i["at_ms"] for i in out["decisions"]] == [0, 5000]              # case / tonos / sigma; spans two -> first
    assert out["nested"]["b"][0]["at_ms"] == 20000


def test_locate_not_found_empty_or_non_string_quote_gets_no_at_ms_and_input_is_not_mutated():
    c = {"items": [item("Θα απολύσουμε τον Νίκο"), item(""), {"text": "t", "quote": None}, {"text": "no quote"}]}
    out = quotes.locate(c, SEGS)
    assert all("at_ms" not in i for i in out["items"])
    c2 = {"items": [item("deploy")]}
    quotes.locate(c2, SEGS)
    assert "at_ms" not in c2["items"][0]


def test_locate_repeated_quote_resolves_to_the_first_occurrence():
    assert quotes.locate({"x": [item("Ωραία")]}, SEGS)["x"][0]["at_ms"] == 12000


def test_locate_skips_segments_that_normalise_to_nothing_and_keeps_other_fields():
    out = quotes.locate({"x": [{"text": "t", "quote": "deploy στις", "verified": True}]}, SEGS)
    assert out["x"][0] == {"text": "t", "quote": "deploy στις", "verified": True, "at_ms": 5000}


def test_locate_without_segments_adds_nothing():
    assert quotes.locate({"x": [item("deploy")]}, []) == {"x": [item("deploy")]}
    assert quotes.locate({"x": [item("deploy")]}, None) == {"x": [item("deploy")]}
