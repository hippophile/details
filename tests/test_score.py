"""Scorer sanity: perfect transcript = 0% wrong, known mistakes are caught, Greek normalisation is consistent."""
import pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "eval"))
import score  # noqa: E402


def test_perfect_transcript_scores_zero():
    ref = score.reference()
    r = score.score(ref, ref)
    assert r["wer_all"] == 0.0
    assert r["tech_terms"] == (100, []) and r["numbers"] == (100, [])


def test_mistakes_are_caught():
    ref = score.reference()
    bad = ref.replace("agents", "έιτζεντς").replace("λοιπον", "")
    r = score.score(ref, bad)
    assert r["wer_all"] > 0
    assert "agents" in r["tech_terms"][1]
    assert "λοιπον" in r["fillers_kept"][1]


def test_greek_normalisation():
    assert score.norm("Οκ, 3.000 ευρώ; Εεε, 70%. Okay!") == "οκ 3000 ευρω εεε 70 τοισ εκατο οκ"
    assert score.norm("Τέλεια") == score.norm("τελεια")  # tonos + case ignored
