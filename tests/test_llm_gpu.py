"""Real LM Studio (gpu marker, run by hand): the already-loaded gemma-4-e4b writes a checklist and tags for a short
Greek transcript. Uses the loaded identifier directly (no load/unload here) and never touches other models."""
import asyncio, json, urllib.request

import pytest

from conftest import make_settings
from details.llm import pipeline, schemas
from details.llm.client import chat
from llm_helpers import SEGS, TEMPLATE

MODEL = "google/gemma-4-e4b"


def loaded(url):
    try:
        with urllib.request.urlopen(url + "/api/v0/models", timeout=3) as r:
            return any(m["id"] == MODEL and m.get("state") == "loaded" for m in json.load(r)["data"])
    except Exception:
        return False


@pytest.mark.gpu
@pytest.mark.parametrize("kind", ["checklist", "tags"])
def test_real_e4b_returns_valid_json_with_verifiable_quotes(tmp_path, kind):
    s = make_settings(tmp_path / "data")
    if not loaded(s.lmstudio.url):
        pytest.skip(f"{MODEL} is not loaded in LM Studio at the configured URL")
    from details.llm.prompts import parse_template
    from details.settings import REPO_ROOT
    t = parse_template(REPO_ROOT / "prompts" / kind / "v1.md")

    async def chat_(messages, *, kind, schema, max_tokens):
        return await chat(s, model=MODEL, kind=kind, schema=schema, messages=messages, max_tokens=max_tokens)

    r = asyncio.run(asyncio.wait_for(pipeline.generate_kind(
        chat_, settings=s, kind=kind, template=t, model=MODEL, context=8192, segments=SEGS, language="Greek",
        title="Test", data_dir=tmp_path), 300))
    last = r.attempts[-1]
    assert last.valid, (last.errors, last.text[:300])
    assert schemas.validate(kind, {k: v for k, v in r.content.items()}, stored=True) == []
    assert r.verified >= 1, r.content
