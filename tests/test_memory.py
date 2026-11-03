import asyncio, dataclasses

import pytest

from conftest import make_settings
from details.llm.lmstudio_ctl import Controller
from details.runner.memory import MemoryManager, MemoryShort
from details.services.models import load_registry
from details.settings import REPO_ROOT
from fakes.lmstudio import FakeLMStudio, FakeMemInfo

REG = load_registry(REPO_ROOT / "models.toml")


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


def mgr(tmp_path, lm, free=64.0, **env):
    return MemoryManager(Controller(lm), REG, make_settings(tmp_path, **env), FakeMemInfo(free))


def test_unload_before_load(tmp_path):
    lm = FakeLMStudio(loaded=[{"identifier": "details-gemma-cheap", "modelKey": "google/gemma-4-e4b"}])
    run(mgr(tmp_path, lm).ensure_llm(REG.llm["gemma-nice"]))
    assert lm.calls == [("unload", "details-gemma-cheap"),
                        ("load", "gemma-4-26b-a4b-it-qat-mlx", "details-gemma-nice", 32768)]


def test_already_loaded_target_is_kept(tmp_path):
    lm = FakeLMStudio(loaded=[{"identifier": "details-gemma-cheap", "modelKey": "google/gemma-4-e4b"}])
    run(mgr(tmp_path, lm).ensure_llm(REG.llm["gemma-cheap"]))
    assert lm.calls == []


def test_foreign_model_never_unloaded(tmp_path):
    lm = FakeLMStudio(loaded=[{"identifier": "my-chat-model", "modelKey": "google/gemma-4-e4b"},
                              {"identifier": "details-gemma-cheap", "modelKey": "google/gemma-4-e4b"}])
    m = mgr(tmp_path, lm)
    run(m.release_for_stt())
    run(m.ensure_llm(REG.llm["gemma-nice"]))
    assert ("unload", "my-chat-model") not in lm.calls
    assert ("unload", "details-gemma-cheap") in lm.calls


def test_exclusive_flag_unloads_foreign(tmp_path):
    lm = FakeLMStudio(loaded=[{"identifier": "my-chat-model", "modelKey": "google/gemma-4-e4b"}])
    run(mgr(tmp_path, lm, DETAILS_LMSTUDIO_EXCLUSIVE="true").release_for_stt())
    assert lm.calls == [("unload", "my-chat-model")]


def test_exclusive_default_false(tmp_path):
    assert make_settings(tmp_path).lmstudio.exclusive is False


def test_preflight_fails_with_numbers(tmp_path):
    lm = FakeLMStudio()
    big = dataclasses.replace(REG.llm["gemma-nice"], needs_gb=14)
    with pytest.raises(MemoryShort) as e:
        run(mgr(tmp_path, lm, free=9).ensure_llm(big))
    assert "needs ~14 GB" in str(e.value) and "9 GB free" in str(e.value)
    assert lm.calls == []   # nothing loaded


def test_preflight_unloads_owned_then_rechecks(tmp_path):
    lm = FakeLMStudio(loaded=[{"identifier": "details-gemma-cheap", "modelKey": "google/gemma-4-e4b"}])
    info = FakeMemInfo(9)
    m = MemoryManager(Controller(lm), REG, make_settings(tmp_path), info)
    orig = lm.unload

    async def unload(ident):
        await orig(ident)
        info.free = 40
    lm.unload = unload
    run(m.ensure_llm(REG.llm["gemma-nice"]))
    assert lm.calls[-1][0] == "load"


def test_release_for_stt_unloads_only_app_models(tmp_path):
    lm = FakeLMStudio(loaded=[{"identifier": "details-gemma-nice", "modelKey": "x"},
                              {"identifier": "google/gemma-4-e4b", "modelKey": "google/gemma-4-e4b"}])
    run(mgr(tmp_path, lm).release_for_stt())
    assert lm.calls == [("unload", "details-gemma-nice")]


def test_release_with_lmstudio_down_does_not_raise(tmp_path):
    run(mgr(tmp_path, FakeLMStudio(reachable=False)).release_for_stt())   # nothing to unload
