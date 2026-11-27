"""Code checks that belong to a prompt version (front matter `checks: a, b`). A check runs inside the same validate
step as the JSON schema, so a failure is an ordinary validation error: one retry with the error text, never repaired.
`run(names, kind, data, text)` is pure: `text` is exactly what the model saw (the whole transcript, or one chunk)."""
import re, unicodedata


def _due_year_in_transcript(kind, data, text):
    """Every checklist item with a due date needs its 4-digit year as a standalone number in the transcript text."""
    if kind != "checklist" or not isinstance(data, dict):
        return []
    errs = []
    for i, it in enumerate(data.get("items") or []):
        due = it.get("due") if isinstance(it, dict) else None
        if not isinstance(due, str) or len(due) < 4:
            continue
        year = due[:4]
        if not re.search(rf"(?<!\d){year}(?!\d)", text or ""):
            errs.append(f"items/{i}/due: the year {year} does not appear in the transcript; use null for due unless "
                        f"the transcript states the year")
    return errs


EXEMPT_KEYS = ("quote", "who", "owner")       # verbatim speech and real names may use any alphabet


def _script(ch):
    """-> the name of a foreign script for a letter that is not Greek or Latin, else None."""
    o = ord(ch)
    if o < 0x250 or 0x370 <= o <= 0x3FF or 0x1F00 <= o <= 0x1FFF:
        return None
    name = unicodedata.name(ch, "")
    if name.startswith(("LATIN", "GREEK")):
        return None
    return name.split(" ")[0].title() if name else "unknown"


def script_issues(data):
    """-> [(path, script)] for every model-written string (not quote/who/owner) holding a letter of another script."""
    out = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k not in EXEMPT_KEYS:
                    walk(v, path + [str(k)])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + [str(i)])
        elif isinstance(node, str):
            found = next((s for s in (_script(c) for c in node if c.isalpha()) if s), None)
            if found:
                out.append(("/".join(path), found))
    walk(data, [])
    return out


def _known_scripts(kind, data, text):
    """Every string the model writes outside quote/who/owner uses only Greek and Latin letters (digits, punctuation and
    symbols are free). A Bengali letter inside a Greek word is the failure this was written for."""
    return [f"{p}: contains {s} letters; write only Greek or English letters" for p, s in script_issues(data)]


CHECKS = {"due_year_in_transcript": _due_year_in_transcript, "known_scripts": _known_scripts}
NAMES = tuple(CHECKS)


def run(names, kind, data, text):
    """-> list of error strings (empty = all checks pass). Unknown names are refused when the prompt file is parsed."""
    errs = []
    for n in names or ():
        errs.extend(CHECKS[n](kind, data, text))
    return errs
