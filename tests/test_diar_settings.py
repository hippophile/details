"""speakers-diarization: [diarization] settings, the Hugging Face token (env/.env only), redaction, scrub_secret_env."""
import asyncio, json, os

import pytest

from conftest import client_for, make_settings
from details.settings import REPO_ROOT, SettingsError, load_settings
from diar_helpers import SENTINEL, TOKEN_NAMES
from fakes.lmstudio import FakeLMStudio, FakeMemInfo
from jobs_helpers import wait_status
from stt_helpers import fake_worker_env, hub_with, rec, TURBO


def test_defaults_without_a_section(tmp_path):
    text = (REPO_ROOT / "config.toml").read_text()
    p = tmp_path / "config.toml"
    p.write_text(text.split("[diarization]")[0])           # a config.toml with no [diarization] section
    s = load_settings(p, env={}, dotenv_path=None)
    d = s.diarization
    assert (d.model, d.device, d.merge_gap_ms, d.stall_s, d.keepalive_s, d.progress_min_interval_s, d.plugins,
            d.hf_token) == ("community-1", "cpu", 1500, 600, 15, 1.0, "", "")


def test_shipped_config_has_the_section_with_the_defaults():
    d = load_settings(env={}, dotenv_path=None).diarization
    assert d.device == "cpu" and d.merge_gap_ms == 1500 and d.model == "community-1"


def test_env_overrides_and_token_aliases(tmp_path):
    s = load_settings(env={"DETAILS_DIARIZATION_DEVICE": "mps", "DETAILS_DIARIZATION_MERGE_GAP_MS": "2000",
                           "HF_TOKEN": SENTINEL}, dotenv_path=None)
    assert s.diarization.device == "mps" and s.diarization.merge_gap_ms == 2000 and s.diarization.hf_token == SENTINEL
    s = load_settings(env={"DETAILS_DIARIZATION_HF_TOKEN": "hf_first" + "a" * 20, "HF_TOKEN": "hf_second" + "b" * 20},
                      dotenv_path=None)
    assert s.diarization.hf_token.startswith("hf_first")           # the DETAILS_ name wins


def test_token_from_dotenv_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"HF_TOKEN={SENTINEL}\n")
    assert load_settings(env={}, dotenv_path=env).diarization.hf_token == SENTINEL


def test_repr_hides_the_token():
    s = load_settings(env={"HF_TOKEN": SENTINEL}, dotenv_path=None)
    assert s.diarization.hf_token == SENTINEL
    assert SENTINEL not in repr(s.diarization) and SENTINEL not in repr(s)


def test_hf_token_in_config_toml_is_refused_without_echoing_the_value(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text((REPO_ROOT / "config.toml").read_text().split("[diarization]")[0]
                 + '[diarization]\nhf_token = "hf_FROMFILEabcdefghijklmnop"\n')
    with pytest.raises(SettingsError) as e:
        load_settings(p, env={}, dotenv_path=None)
    msg = str(e.value)
    assert "HF_TOKEN" in msg and "diarization.hf_token" in msg and "hf_FROMFILE" not in msg


def test_bad_device_names_the_key_and_allowed_values():
    with pytest.raises(SettingsError) as e:
        load_settings(env={"DETAILS_DIARIZATION_DEVICE": "cuda"}, dotenv_path=None)
    assert "diarization.device" in str(e.value) and "cpu" in str(e.value) and "mps" in str(e.value)


@pytest.mark.parametrize("key,val", [("MERGE_GAP_MS", "-1"), ("STALL_S", "0"), ("KEEPALIVE_S", "0"),
                                     ("PROGRESS_MIN_INTERVAL_S", "-1")])
def test_other_bad_values(key, val):
    with pytest.raises(SettingsError, match="diarization"):
        load_settings(env={f"DETAILS_DIARIZATION_{key}": val}, dotenv_path=None)


# ---- redaction ----------------------------------------------------------------------------------------------

def test_redact_masks_hf_pattern_with_no_configured_secrets():
    from details.runner.joblog import redact
    out = redact("401 for token hf_abcdefghijklmnopqrstuvwxyz0123456789 now")
    assert "hf_abcdefghijklmnop" not in out and "***" in out
    assert redact("the file hf_short and hf_ok stay") == "the file hf_short and hf_ok stay"      # fewer than 20 chars


def test_secrets_from_includes_the_token(tmp_path):
    from details.runner.joblog import redact, secrets_from
    s = make_settings(tmp_path, HF_TOKEN="notAnHfShapedToken_123")
    assert "notAnHfShapedToken_123" in secrets_from(s)
    assert "notAnHfShapedToken_123" not in redact("boom notAnHfShapedToken_123", secrets_from(s))


# ---- scrub_secret_env ---------------------------------------------------------------------------------------

def test_scrub_removes_the_three_names_and_keeps_the_setting():
    from details.settings import scrub_secret_env
    env = {n: SENTINEL for n in TOKEN_NAMES}
    env["PATH"] = "/usr/bin"
    s = load_settings(env=env, dotenv_path=None)
    scrub_secret_env(env)
    assert env == {"PATH": "/usr/bin"} and s.diarization.hf_token == SENTINEL


def test_scrub_with_nothing_set_is_fine():
    from details.settings import scrub_secret_env
    env = {"A": "1"}
    scrub_secret_env(env)
    assert env == {"A": "1"}


def test_main_scrubs_before_create_app(monkeypatch, tmp_path):
    from aiohttp import web

    from details import __main__ as m
    order = []
    real = m.load_settings
    monkeypatch.setattr(m, "load_settings", lambda: real(env={}, dotenv_path=None))
    monkeypatch.setattr(m, "scrub_secret_env", lambda *a, **k: order.append("scrub"))
    monkeypatch.setattr(m, "create_app", lambda s: order.append("create_app") or object())
    monkeypatch.setattr(web, "run_app", lambda *a, **k: order.append("run_app"))
    monkeypatch.setenv("DETAILS_PATHS_DATA_DIR", str(tmp_path / "d"))
    m.main()
    assert order == ["scrub", "create_app", "run_app"]


def test_stt_worker_started_through_main_has_no_token_in_its_environment(db, db_url, mk_settings, tmp_path, monkeypatch):
    from aiohttp import web

    from details import __main__ as m
    fake_worker_env(monkeypatch, tmp_path)
    envfile = tmp_path / "stt_env.json"
    monkeypatch.setenv("FAKE_STT_ENVFILE", str(envfile))
    monkeypatch.setenv("DETAILS_STT_PLUGINS", "fakes.stt")
    monkeypatch.setenv("DETAILS_PATHS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DETAILS_PATHS_HF_HUB", str(hub_with(tmp_path, TURBO)))
    monkeypatch.setenv("DETAILS_STT_WINDOW_S", "1")
    monkeypatch.setenv("DATABASE_URL", str(db_url))
    for n in TOKEN_NAMES:                       # register for restore: load_settings(.env) and a failed scrub must not leak
        monkeypatch.setenv(n, "x")
        monkeypatch.delenv(n)
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"HF_TOKEN={SENTINEL}\n")           # what a real .env does: merged into os.environ at load
    real = m.load_settings
    monkeypatch.setattr(m, "load_settings", lambda: real(dotenv_path=dotenv))
    real_create = m.create_app
    monkeypatch.setattr(m, "create_app", lambda s: real_create(s, lm_transport=FakeLMStudio(), meminfo=FakeMemInfo()))
    captured = {}
    monkeypatch.setattr(web, "run_app", lambda app, **k: captured.update(app=app))
    rid = rec(db, mk_settings(db_url), seconds=2)
    m.main()
    assert "HF_TOKEN" not in os.environ

    async def go():
        async with client_for(captured["app"]) as c:
            r = await c.post(f"/api/recordings/{rid}/stt-runs", json={"model_id": "whisper-turbo", "language": "el"})
            assert r.status == 202
            job = (await r.json())["job_id"]
            from details.api.jobs import JOBS
            await wait_status(captured["app"][JOBS], job, "done")
    asyncio.run(asyncio.wait_for(go(), 40))
    assert json.loads(envfile.read_text()) == {n: False for n in TOKEN_NAMES}
