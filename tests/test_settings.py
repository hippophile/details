import pytest

from details.settings import SettingsError, load_settings

CFG = """
[server]
port = 8765
[paths]
data_dir = "~/details-data"
hf_hub = "~/.cache/huggingface/hub"
[lmstudio]
url = "http://localhost:1234"
cli = "/opt/lms"
exclusive = false
[db]
url = ""
url_test = ""
connect_timeout = 5
[record]
sample_rate = 16000
sync_interval_s = 5
grace_s = 60
keep_wav = false
min_free_gb = 2
max_msg_mb = 1
ffmpeg = ""
ffprobe = ""
tz = "Europe/Athens"
[trash]
days = 30
[import]
max_mb = 2048
[models]
path = "models.toml"
[runner]
heartbeat_timeout_s = 120
cancel_grace_s = 5
"""


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(CFG)
    return p


def load(cfg, env=None, dotenv=None):
    return load_settings(cfg, env=env or {}, dotenv_path=dotenv)


def test_defaults_from_file(cfg):
    s = load(cfg)
    assert s.server.port == 8765
    assert s.paths.data_dir.is_absolute() and s.paths.data_dir.name == "details-data"
    assert "~" not in str(s.paths.data_dir)


def test_env_overrides_file(cfg):
    assert load(cfg, {"DETAILS_PORT": "9000"}).server.port == 9000
    assert load(cfg, {"DETAILS_SERVER_PORT": "9001", "DETAILS_PORT": "9000"}).server.port == 9001


def test_port_alias(cfg):
    assert load(cfg, {"PORT": "9100"}).server.port == 9100


def test_dotenv_does_not_override_env(cfg, tmp_path):
    d = tmp_path / ".env"
    d.write_text("# c\nDATABASE_URL=postgresql://a/details\nDETAILS_PORT='9200'\nexport X=1\n")
    s = load(cfg, {"DATABASE_URL": "postgresql://b/details"}, d)
    assert s.db.url == "postgresql://b/details"
    assert s.server.port == 9200  # .env fills what the environment lacks


def test_dotenv_missing_file_is_fine(cfg, tmp_path):
    assert load(cfg, dotenv=tmp_path / "nope").server.port == 8765


def test_bad_toml_names_file(tmp_path):
    p = tmp_path / "broken.toml"
    p.write_text("[server\nport = ")
    with pytest.raises(SettingsError, match="broken.toml"):
        load_settings(p, env={}, dotenv_path=None)


def test_nested_lmstudio_section(cfg):
    s = load(cfg, {"DETAILS_LMSTUDIO_URL": "http://localhost:5555"})
    assert s.lmstudio.url == "http://localhost:5555"
    assert s.lmstudio.cli == "/opt/lms"


def test_validation_hook_rejects_bad_port(cfg):
    with pytest.raises(SettingsError, match=r"server\.port"):
        load(cfg, {"DETAILS_SERVER_PORT": "notanumber"})
    with pytest.raises(SettingsError, match=r"server\.port"):
        load(cfg, {"DETAILS_SERVER_PORT": "70000"})


def test_hf_home_sets_hub(cfg):
    assert str(load(cfg, {"HF_HOME": "/x/hf"}).paths.hf_hub) == "/x/hf/hub"


def test_settings_is_frozen(cfg):
    with pytest.raises(Exception):
        load(cfg).server.port = 1


def test_record_section_defaults_env_and_validation():
    from details.settings import SettingsError, load_settings
    s = load_settings(env={}, dotenv_path=None)
    assert s.record.sample_rate == 16000 and s.record.keep_wav is False and s.record.grace_s == 60
    assert load_settings(env={"DETAILS_RECORD_GRACE_S": "5"}, dotenv_path=None).record.grace_s == 5
    assert load_settings(env={"DETAILS_RECORD_KEEP_WAV": "true"}, dotenv_path=None).record.keep_wav is True
    import pytest
    with pytest.raises(SettingsError, match="record.sample_rate"):
        load_settings(env={"DETAILS_RECORD_SAMPLE_RATE": "0"}, dotenv_path=None)
