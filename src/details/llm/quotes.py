"""Verbatim quote check. norm() is loaded from eval/score.py by path (read-only reuse, the file is not edited):
case, tonos, final sigma and punctuation do not matter; a quote must still be a substring of the transcript."""
import copy, functools, importlib.util

from ..settings import REPO_ROOT


@functools.cache
def _score():
    spec = importlib.util.spec_from_file_location("details_eval_score", REPO_ROOT / "eval" / "score.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def norm(s):
    return _score().norm(s)


def verify(content, full_text):
    """-> (copy of content with `verified` on every dict that has a `quote`, n_verified, n_unverified).
    Items are never removed or edited. An empty quote is unverified."""
    hay = norm(full_text)
    out, counts = copy.deepcopy(content), [0, 0]

    def walk(node):
        if isinstance(node, dict):
            if "quote" in node:
                q = norm(node["quote"]) if isinstance(node["quote"], str) else ""
                ok = bool(q) and q in hay
                node["verified"] = ok
                counts[0 if ok else 1] += 1
            for v in list(node.values()):
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(out)
    return out, counts[0], counts[1]


def locate(content, segments):
    """-> copy of content with `at_ms` (start of the segment where the quote begins) on every dict whose normalised quote
    is found in `segments` [(start_ms, text)]. Each segment is normalised alone and joined by one space (= norm of the
    joined text). A quote spanning segments resolves to the first; a repeated quote to its first occurrence."""
    out = copy.deepcopy(content)
    hay, starts, pos = [], [], 0
    for ms, text in segments or ():
        t = norm(text)
        if t:
            starts.append((pos, ms))
            hay.append(t)
            pos += len(t) + 1
    hay = " ".join(hay)

    def walk(node):
        if isinstance(node, dict):
            q = norm(node["quote"]) if isinstance(node.get("quote"), str) else ""
            at = hay.find(q) if q else -1
            if at >= 0:
                node["at_ms"] = next(ms for p, ms in reversed(starts) if p <= at)
            for v in list(node.values()):
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(out)
    return out
