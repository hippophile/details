"""JSON Schemas per output kind (schemas/<kind>.v1.json, draft 2020-12). `validate` checks what the model sent
(code-written `verified` is not allowed there) or, with stored=True, what is kept in output.content."""
import copy, functools, json

from jsonschema import Draft202012Validator

from ..settings import REPO_ROOT

KINDS = ("debrief", "notes", "checklist", "tags")   # diagram and speakers are v2.1
SCHEMAS_DIR = REPO_ROOT / "schemas"


@functools.cache
def _load(kind):
    return json.loads((SCHEMAS_DIR / f"{kind}.v1.json").read_text(encoding="utf-8"))


def load_schema(kind):
    return copy.deepcopy(_load(kind))


def _strip_verified(node):
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            props.pop("verified", None)
            if "required" in node:
                node["required"] = [r for r in node["required"] if r != "verified"]
        for v in node.values():
            _strip_verified(v)
    elif isinstance(node, list):
        for v in node:
            _strip_verified(v)


@functools.cache
def _model(kind):
    s = copy.deepcopy(_load(kind))
    _strip_verified(s)
    return s


def model_schema(kind):
    """The schema sent to the model and used to validate its reply: no `verified` anywhere."""
    return copy.deepcopy(_model(kind))


@functools.cache
def _validator(kind, stored):
    return Draft202012Validator(_load(kind) if stored else _model(kind))


def validate(kind, data, stored=False):
    """-> list of readable error strings; empty = valid."""
    errs = sorted(_validator(kind, stored).iter_errors(data), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message[:200]}" for e in errs[:10]]
