"""Code checks that belong to a prompt version (front matter `checks: a, b`). A check runs inside the same validate
step as the JSON schema, so a failure is an ordinary validation error: one retry with the error text, never repaired.
`run(names, kind, data, text)` is pure: `text` is exactly what the model saw (the whole transcript, or one chunk)."""
import re


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


CHECKS = {"due_year_in_transcript": _due_year_in_transcript}
NAMES = tuple(CHECKS)


def run(names, kind, data, text):
    """-> list of error strings (empty = all checks pass). Unknown names are refused when the prompt file is parsed."""
    errs = []
    for n in names or ():
        errs.extend(CHECKS[n](kind, data, text))
    return errs
