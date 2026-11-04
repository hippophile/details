"""Script-based scoring: eval/score.py (v1, loaded by path, never edited) applied to a script's stored body_md."""
import functools, importlib.util, re

from ..settings import REPO_ROOT

SCORE_PATH = REPO_ROOT / "eval" / "score.py"
LINES = re.compile(r"^\*\*[^*:]+:\*\*\s*(.+)$", re.M)     # same pattern as score.reference()
RECALL = ("tech_terms", "fillers_kept", "hesitations_kept", "numbers")


@functools.lru_cache(maxsize=1)
def load_score():
    spec = importlib.util.spec_from_file_location("details_eval_score", SCORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reference_text(body_md):
    """Normalised reference from the `**Name:** text` lines of a script body ('' when there are none)."""
    lines = LINES.findall(body_md or "")
    return load_score().norm(" ".join(lines)) if lines else ""


def score_run(body_md, transcript_text):
    """-> metrics dict (recall entries as {pct, missed}), or None when the script has no reference lines."""
    ref = reference_text(body_md)
    if not ref:
        return None
    r = load_score().score(ref, transcript_text or "")
    out = {k: v for k, v in r.items() if k not in RECALL}
    for k in RECALL:
        out[k] = {"pct": r[k][0], "missed": r[k][1]}
    return out
