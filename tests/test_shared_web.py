import re

from details.settings import REPO_ROOT

CSS = (REPO_ROOT / "web/shared/shared.css").read_text()
JS = (REPO_ROOT / "web/shared/nav.js").read_text()


def test_css_has_tokens_and_dark_block():
    for t in ("bg", "card", "fg", "muted", "line", "good", "warn", "bad", "accent"):
        assert re.search(rf"--{t}\s*:", CSS), t
    assert "prefers-color-scheme: dark" in CSS
    for sel in (".card", ".tab", "max-width:820px"):
        assert sel in CSS.replace(" ", "") or sel in CSS


def test_nav_lists_entries_in_order():
    i = [JS.index(x) for x in ("Record", "Library", "Analyse")]
    assert i == sorted(i)
    for p in ("/record", "/library", "/analyse"):
        assert p in JS
    assert 'getElementById("nav")' in JS or "getElementById('nav')" in JS
    assert "aria-current" in JS
