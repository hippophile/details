import asyncio

import pytest

from conftest import client_for, make_settings
from details.app import create_app
from details.services.models import ModelsConfigError, load_registry
from details.settings import REPO_ROOT
from fakes.lmstudio import FakeLMStudio, fixture

SHIPPED = REPO_ROOT / "models.toml"


def toml(tmp_path, text):
    p = tmp_path / "models.toml"
    p.write_text(text)
    return p


STT_OK = '[stt.a]\nengine="mlx_whisper"\nlabel="A"\nrepo="x/y"\nneeds_gb=2\nrole="fast"\n'
LLM_OK = '[llm.b]\nengine="lmstudio"\nlabel="B"\nmodel="k"\ncontext=4096\nneeds_gb=5\nrole="cheap"\n'


def test_shipped_entries_load():
    r = load_registry(SHIPPED)
    assert sorted(r.stt) == ["whisper-large", "whisper-turbo"]
    assert sorted(r.llm) == ["gemma-12b", "gemma-cheap", "gemma-nice", "krikri-8b"]
    assert r.llm["gemma-cheap"].role == "cheap" and r.llm["gemma-nice"].role == "nice"
    assert r.stt["whisper-turbo"].engine == r.stt["whisper-large"].engine == "mlx_whisper"
    assert r.llm["gemma-nice"].engine == "lmstudio"


def test_shipped_keys_exist_in_recorded_lms_ls():
    keys = {m["modelKey"] for m in fixture("lms_ls.json")}
    r = load_registry(SHIPPED)
    assert all(m.model in keys for m in r.llm.values())


def test_extra_block_is_listed_without_code(tmp_path):
    r = load_registry(toml(tmp_path, SHIPPED.read_text() + LLM_OK.replace("llm.b", "llm.test-model")))
    assert "test-model" in r.llm and len(r.llm) == 5


@pytest.mark.parametrize("text,entry,needle", [
    (STT_OK.replace("mlx_whisper", "whisperx"), "stt.a", "mlx_whisper"),
    (LLM_OK.replace("lmstudio", "mlx_whisper"), "llm.b", "lmstudio"),
    (STT_OK.replace('repo="x/y"\n', ""), "stt.a", "repo"),
    (LLM_OK.replace('model="k"\n', ""), "llm.b", "model"),
    (STT_OK.replace('"fast"', '"turbo"'), "stt.a", "role"),
])
def test_invalid_entry_is_named(tmp_path, text, entry, needle):
    with pytest.raises(ModelsConfigError) as e:
        load_registry(toml(tmp_path, text))
    assert entry in str(e.value) and needle in str(e.value)


def test_bad_toml_and_missing_file_name_the_file(tmp_path):
    with pytest.raises(ModelsConfigError, match="models.toml"):
        load_registry(toml(tmp_path, "[stt.a\nengine ="))
    with pytest.raises(ModelsConfigError, match="nope.toml"):
        load_registry(tmp_path / "nope.toml")


def test_settings_models_path(tmp_path):
    assert make_settings(tmp_path).models.path == "models.toml"
    assert make_settings(tmp_path, DETAILS_MODELS_PATH="/x/m.toml").models.path == "/x/m.toml"


# ---- /api/models --------------------------------------------------------------------------------------

def hub_with(tmp_path, *repos):
    for repo in repos:
        d = tmp_path / "hf" / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots" / "abc"
        d.mkdir(parents=True)
        (d / "weights.safetensors").write_bytes(b"x")
    return {"HF_HOME": str(tmp_path / "hf")}


def get(tmp_path, path, lm=None, **env):
    async def go():
        s = make_settings(tmp_path / "data", **env)
        async with client_for(create_app(s, lm_transport=lm or FakeLMStudio())) as c:
            r = await c.get(path)
            return r.status, await r.json()
    return asyncio.run(asyncio.wait_for(go(), 20))


def test_installed_flags(tmp_path):
    status, body = get(tmp_path, "/api/models", **hub_with(tmp_path, "mlx-community/whisper-large-v3-turbo"))
    assert status == 200
    stt = {m["id"]: m for m in body["stt"]}
    assert stt["whisper-turbo"]["installed"] is True and stt["whisper-large"]["installed"] is False
    assert stt["whisper-large"]["unavailable_reason"]
    assert body["lmstudio"] == {"reachable": True}
    assert all(m["installed"] for m in body["llm"])


def test_llm_not_downloaded(tmp_path):
    only_e4b = [m for m in fixture("lms_ls.json") if m["modelKey"] == "google/gemma-4-e4b"]
    _, body = get(tmp_path, "/api/models", lm=FakeLMStudio(ls=only_e4b))
    llm = {m["id"]: m for m in body["llm"]}
    assert llm["gemma-cheap"]["installed"] is True and llm["gemma-nice"]["installed"] is False


def test_entry_shape_and_roles(tmp_path):
    _, body = get(tmp_path, "/api/models")
    for m in body["stt"] + body["llm"]:
        assert {"id", "kind", "label", "engine", "role", "needs_gb", "installed"} <= set(m)
        assert m["role"] in ("cheap", "nice", "fast", "accurate")
    assert {m["kind"] for m in body["stt"]} == {"stt"} and {m["kind"] for m in body["llm"]} == {"llm"}


def test_kind_filter_flat_list(tmp_path):
    status, body = get(tmp_path, "/api/models?kind=llm")
    assert status == 200 and isinstance(body, list) and [m["id"] for m in body] == ["gemma-cheap", "gemma-nice", "krikri-8b", "gemma-12b"]
    assert get(tmp_path, "/api/models?kind=stt")[1][0]["kind"] == "stt"
    assert get(tmp_path, "/api/models?kind=tts")[0] == 422


def test_lmstudio_unreachable_is_200(tmp_path):
    status, body = get(tmp_path, "/api/models", lm=FakeLMStudio(reachable=False))
    assert status == 200 and body["lmstudio"] == {"reachable": False}
    assert all(m["installed"] is False and m["unavailable_reason"] for m in body["llm"])
