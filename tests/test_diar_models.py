"""speakers-diarization: [diar.<id>] registry blocks and the availability branches of /api/models (no torch, no crash)."""
import asyncio, importlib.util, json, subprocess, sys

import pytest

from conftest import client_for, make_settings
from details.app import create_app
from details.services import models
from details.services.models import ModelsConfigError, load_registry
from details.settings import REPO_ROOT, SettingsError
from diar_helpers import REPO_DIR, SENTINEL, diar_hub, diar_ready
from fakes.lmstudio import FakeLMStudio, FakeMemInfo

SHIPPED = REPO_ROOT / "models.toml"
DIAR_OK = ('[diar.x]\nengine="pyannote"\nlabel="X"\nrepo="a/b"\nneeds_gb=3\nrole="accurate"\n')
REPO = "pyannote/speaker-diarization-community-1"


def toml(tmp_path, text):
    p = tmp_path / "models.toml"
    p.write_text(text)
    return p


# ---- registry -----------------------------------------------------------------------------------------------

def test_shipped_has_exactly_one_diar_entry():
    r = load_registry(SHIPPED)
    assert list(r.diar) == ["community-1"]
    m = r.diar["community-1"]
    assert (m.engine, m.repo, m.kind, m.role) == ("pyannote", REPO, "diar", "accurate")


def test_registry_without_diar_blocks_has_an_empty_dict(tmp_path):
    r = load_registry(toml(tmp_path, SHIPPED.read_text().split("[diar.")[0]))
    assert r.diar == {} and "whisper-turbo" in r.stt


@pytest.mark.parametrize("text,needle", [(DIAR_OK.replace("pyannote", "whisperx"), "pyannote"),
                                         (DIAR_OK.replace("pyannote", "lmstudio"), "pyannote"),
                                         (DIAR_OK.replace('repo="a/b"\n', ""), "repo")])
def test_invalid_diar_entry_is_named(tmp_path, text, needle):
    with pytest.raises(ModelsConfigError) as e:
        load_registry(toml(tmp_path, text))
    assert "diar.x" in str(e.value) and needle in str(e.value)


def test_stt_and_llm_engines_still_refuse_pyannote(tmp_path):
    for block in ('[stt.a]\nengine="pyannote"\nlabel="A"\nrepo="x/y"\nneeds_gb=2\nrole="fast"\n',
                  '[llm.b]\nengine="pyannote"\nlabel="B"\nmodel="k"\ncontext=4096\nneeds_gb=5\nrole="cheap"\n'):
        with pytest.raises(ModelsConfigError):
            load_registry(toml(tmp_path, block))


def test_configured_diarization_model_must_exist(mk_settings):
    s = mk_settings(None, DETAILS_DIARIZATION_MODEL="nope")
    with pytest.raises(SettingsError) as e:
        create_app(s, lm_transport=FakeLMStudio(), meminfo=FakeMemInfo())
    assert "diarization.model" in str(e.value) and "nope" in str(e.value)


# ---- /api/models --------------------------------------------------------------------------------------------

def get(mk_settings, path, hub=None, **env):
    async def go():
        s = mk_settings(None, **({"DETAILS_PATHS_HF_HUB": str(hub)} if hub else {}), **env)
        async with client_for(create_app(s, lm_transport=FakeLMStudio(), meminfo=FakeMemInfo())) as c:
            r = await c.get(path)
            return r.status, await r.json()
    return asyncio.run(asyncio.wait_for(go(), 30))


def diar_entry(mk_settings, hub, **env):
    status, body = get(mk_settings, "/api/models", hub, **env)
    assert status == 200 and "diar" in body
    assert [e["id"] for e in body["diar"]] == ["community-1"]
    return body["diar"][0]


def test_kind_filter_diar_and_unknown_kind(mk_settings, tmp_path):
    status, body = get(mk_settings, "/api/models?kind=diar", diar_hub(tmp_path, "missing"))
    assert status == 200 and isinstance(body, list) and body[0]["id"] == "community-1"
    assert body[0]["kind"] == "diar" and body[0]["engine"] == "pyannote" and body[0]["role"] == "accurate"
    assert get(mk_settings, "/api/models?kind=x")[0] == 422
    assert [e["id"] for e in get(mk_settings, "/api/models?kind=stt")[1]] == ["whisper-turbo", "whisper-large"]


def test_package_genuinely_absent_in_an_interpreter_without_site_packages(tmp_path):
    """No patching of the seam: `python -S` has no site-packages, so pyannote is really not importable."""
    code = ("import sys, json, asyncio\n"
            f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r})\n"
            "from details.services import models\n"
            "from details.settings import load_settings\n"
            "assert 'pyannote' not in sys.modules\n"
            "s = load_settings(env={'DETAILS_PATHS_HF_HUB': sys.argv[1]}, dotenv_path=None)\n"
            "reg = models.load_registry(models.resolve_path(s))\n"
            "class Ctl:\n"
            "    async def reachable(self): return False\n"
            "out = asyncio.run(models.describe(reg, s, Ctl()))\n"
            "print(json.dumps({'importable': models.pyannote_importable(), 'diar': out['diar'], 'torch': 'torch' in sys.modules}))\n")
    r = subprocess.run([sys.executable, "-S", "-c", code, str(diar_hub(tmp_path, "missing"))], capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-600:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    e = got["diar"][0]
    assert got["importable"] is False and got["torch"] is False
    assert e["installed"] is False and "not installed" in e["unavailable_reason"] and "make setup-diar" in e["hint"]


def test_api_with_nothing_installed_is_200_and_the_app_starts(mk_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(models, "pyannote_importable", lambda: False)
    e = diar_entry(mk_settings, diar_hub(tmp_path, "missing"))
    assert e["installed"] is False and "make setup-diar" in e["hint"]


def test_seam_returns_false_when_find_spec_raises(monkeypatch):
    for exc in (ModuleNotFoundError("pyannote"), ValueError("bad"), ImportError("x")):
        def boom(name, *a, **k):
            raise exc
        monkeypatch.setattr(importlib.util, "find_spec", boom)
        assert models.pyannote_importable() is False


def test_seam_checks_the_parent_package_first(monkeypatch):
    seen = []

    def spy(name, *a, **k):
        seen.append(name)
        return None
    monkeypatch.setattr(importlib.util, "find_spec", spy)
    assert models.pyannote_importable() is False
    assert seen[0] == "pyannote"


def test_model_missing_no_token(mk_settings, tmp_path, monkeypatch):
    diar_ready(monkeypatch)
    e = diar_entry(mk_settings, diar_hub(tmp_path, "missing"))
    assert e["installed"] is False and "not downloaded" in e["unavailable_reason"]
    h = e["hint"]
    for needle in ("free Hugging Face token", "terms", "HF_TOKEN", ".env", "make diar-model"):
        assert needle in h, needle


def test_model_missing_token_set(mk_settings, tmp_path, monkeypatch):
    diar_ready(monkeypatch)
    e = diar_entry(mk_settings, diar_hub(tmp_path, "missing"), HF_TOKEN=SENTINEL)
    assert e["installed"] is False and "make diar-model" in e["hint"] and "terms" in e["hint"]
    assert "create a free" not in e["hint"]
    assert SENTINEL not in json.dumps(e)


def test_partial_download_is_not_installed(mk_settings, tmp_path, monkeypatch):
    diar_ready(monkeypatch)
    e = diar_entry(mk_settings, diar_hub(tmp_path, "partial"))
    assert e["installed"] is False and "incomplete" in e["unavailable_reason"]
    assert "make diar-model" in e["hint"] and "again" in e["hint"]


def test_cached_model_without_token_is_installed(mk_settings, tmp_path, monkeypatch):
    diar_ready(monkeypatch)
    e = diar_entry(mk_settings, diar_hub(tmp_path, "complete"))
    assert e["installed"] is True and "unavailable_reason" not in e and "hint" not in e


def test_no_hint_or_reason_contains_the_token(mk_settings, tmp_path, monkeypatch):
    for state in ("missing", "partial", "complete"):
        for imp in (True, False):
            monkeypatch.setattr(models, "pyannote_importable", lambda imp=imp: imp)
            e = diar_entry(mk_settings, diar_hub(tmp_path, state), HF_TOKEN=SENTINEL)
            assert SENTINEL not in json.dumps(e)


def test_model_cached_needs_config_and_a_weight_file(tmp_path):
    for name in ("complete", "partial", "weights_only"):
        (tmp_path / name).mkdir()
    assert models.model_cached(diar_hub(tmp_path / "complete", "complete"), REPO) is True
    assert models.model_cached(diar_hub(tmp_path / "partial", "partial"), REPO) is False
    assert models.model_cached(tmp_path / "nothing", REPO) is False
    snap = tmp_path / "weights_only" / "hub" / REPO_DIR / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "w.safetensors").write_bytes(b"x")                       # a weight file but no config.yaml
    assert models.model_cached(tmp_path / "weights_only" / "hub", REPO) is False


@pytest.mark.parametrize("weight", ["pytorch_model.bin", "model.safetensors", "epoch.ckpt"])
def test_any_known_weight_extension_counts(tmp_path, weight):
    hub = diar_hub(tmp_path, "partial")
    (hub / "models--pyannote--speaker-diarization-community-1" / "snapshots" / "abc" / weight).write_bytes(b"x")
    assert models.model_cached(hub, REPO) is True


def test_describe_does_not_import_torch(tmp_path):
    code = ("import sys, asyncio\n"
            f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r})\n"
            "from details.services import models\n"
            "from details.settings import load_settings\n"
            "s = load_settings(env={'DETAILS_PATHS_HF_HUB': sys.argv[1]}, dotenv_path=None)\n"
            "reg = models.load_registry(models.resolve_path(s))\n"
            "class Ctl:\n"
            "    async def reachable(self): return False\n"
            "asyncio.run(models.describe(reg, s, Ctl()))\n"
            "models.pyannote_importable()\n"
            "print('torch' in sys.modules, 'pyannote.audio' in sys.modules)\n")
    r = subprocess.run([sys.executable, "-c", code, str(diar_hub(tmp_path, "complete"))], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr[-600:]
    assert r.stdout.split()[-2:] == ["False", "False"]          # the check uses find_spec, never imports the package
