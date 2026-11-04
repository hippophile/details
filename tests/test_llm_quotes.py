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
