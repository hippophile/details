"""compare-runs: services.scoring.score_run gives exactly the numbers eval/score.py gives; eval/score.py is untouched."""
import pathlib, sys

import pytest

from details.services import scoring
from details.settings import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "eval"))
import score  # noqa: E402

BODY = (REPO_ROOT / "eval" / "script_meeting_el_en.md").read_text()


def hyps():
    ref = score.reference()
    return {"perfect": ref, "one_tech_dropped": ref.replace("agents", "", 1).replace("pgvector", "pg vektor"),
            "all_different": "τελείως διαφορετικό κείμενο που δεν μοιάζει σε τίποτα"}


def flat(r):
    return {"wer_all": r["wer_all"], "wer_no_hesitations": r["wer_no_hesitations"],
            **{k: (r[k][0], r[k][1]) for k in ("tech_terms", "fillers_kept", "hesitations_kept", "numbers")}}


@pytest.mark.parametrize("name", ["perfect", "one_tech_dropped", "all_different"])
def test_score_run_matches_v1_scorer_metric_for_metric(name):
    hyp = hyps()[name]
    want = flat(score.score(score.reference(), hyp))
    got = scoring.score_run(BODY, hyp)
    assert got["wer_all"] == want["wer_all"] and got["wer_no_hesitations"] == want["wer_no_hesitations"]
    for k in ("tech_terms", "fillers_kept", "hesitations_kept", "numbers"):
        assert (got[k]["pct"], got[k]["missed"]) == want[k], k


def test_the_perfect_and_dropped_cases_mean_something():
    p, d = scoring.score_run(BODY, hyps()["perfect"]), scoring.score_run(BODY, hyps()["one_tech_dropped"])
    assert p["wer_all"] == 0.0 and p["tech_terms"]["pct"] == 100
    assert d["wer_all"] > 0 and "agents" in d["tech_terms"]["missed"]


def test_front_matter_free_body_with_names_and_text():
    body = "**Ελένη:** Καλημέρα. Τι κάνεις;\n\n**Νίκος:** Καλά.\n"
    assert scoring.reference_text(body) == score.norm("Καλημέρα. Τι κάνεις; Καλά.")


def test_no_reference_lines_gives_none_and_never_raises():
    assert scoring.score_run("just prose\nwith no speaker lines", "anything") is None
    assert scoring.score_run("", "") is None
    assert scoring.score_run(BODY, None) is not None      # an empty hypothesis is a valid (bad) transcript


def test_score_py_is_loaded_by_path_from_the_eval_dir():
    assert pathlib.Path(scoring.load_score().__file__) == REPO_ROOT / "eval" / "score.py"
