"""llm-outputs: JSON Schemas per kind."""
import pytest

from details.llm import schemas
from llm_helpers import GOOD, good


def test_four_kinds_only():
    assert schemas.KINDS == ("debrief", "notes", "checklist", "tags")
    for k in schemas.KINDS:
        s = schemas.load_schema(k)
        assert s["additionalProperties"] is False and s["properties"]["schema_version"]["const"] == 1


@pytest.mark.parametrize("kind", ["debrief", "notes", "checklist", "tags"])
def test_good_fixture_passes(kind):
    assert schemas.validate(kind, good(kind)) == []


def test_checklist_due_null_and_iso_date_ok():
    c = good("checklist")
    assert c["items"][0]["due"] == "2026-11-12" and c["items"][0]["owner"] is None
    assert schemas.validate("checklist", c) == []


def mut(kind, fn):
    c = good(kind)
    fn(c)
    return schemas.validate(kind, c)


@pytest.mark.parametrize("fn", [
    lambda c: c["items"][0].update(due="next week"),
    lambda c: c["items"][0].update(due="2026-13-45x"),
    lambda c: c["items"][0].pop("quote"),
    lambda c: c["items"][0].update(extra=1),
    lambda c: c.update(extra=1),
    lambda c: c["items"][0].update(done="yes"),
    lambda c: c.update(schema_version=2),
])
def test_bad_checklists_fail(fn):
    assert mut("checklist", fn)


@pytest.mark.parametrize("kind,fn", [
    ("debrief", lambda c: c["decisions"][0].pop("quote")),
    ("debrief", lambda c: c["risks"][0].update(severity="huge")),
    ("notes", lambda c: c["sections"][0]["bullets"][0].pop("quote")),
    ("tags", lambda c: c["tags"][0].update(name="Not Kebab")),
    ("tags", lambda c: c.update(tags=[{"name": f"t{i}", "quote": "q"} for i in range(9)])),
])
def test_other_bad_fixtures_fail(kind, fn):
    assert mut(kind, fn)


def test_greek_kebab_tag_accepted():
    c = good("tags")
    c["tags"][0]["name"] = "ασφάλεια-δεδομένων"
    assert schemas.validate("tags", c) == []


def test_verified_is_code_written_only():
    c = good("checklist")
    c["items"][0]["verified"] = True
    assert schemas.validate("checklist", c, stored=True) == []
    assert schemas.validate("checklist", c)        # a model must not write it


def test_model_schema_has_no_verified_property_anywhere():
    for k in schemas.KINDS:
        assert "verified" not in str(schemas.model_schema(k))
