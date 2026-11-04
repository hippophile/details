"""Layered settings: config.toml < DETAILS_<SECTION>_<KEY> env (with .env filling what the env lacks).
The only module that reads the environment. DSNs and .env values never appear in errors or logs."""
import dataclasses, os, pathlib, tomllib
from dataclasses import dataclass

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.toml"
DOTENV_PATH = REPO_ROOT / ".env"


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Server:
    port: int


@dataclass(frozen=True)
class Paths:
    data_dir: pathlib.Path
    hf_hub: pathlib.Path


@dataclass(frozen=True)
class LmStudio:
    url: str
    cli: str
    exclusive: bool   # true = the memory manager may also unload models it did not load


@dataclass(frozen=True)
class Db:
    url: str
    url_test: str
    connect_timeout: int


@dataclass(frozen=True)
class Record:
    sample_rate: int
    sync_interval_s: float
    grace_s: float
    keep_wav: bool
    min_free_gb: float
    max_msg_mb: int
    ffmpeg: str
    ffprobe: str
    tz: str


@dataclass(frozen=True)
class Trash:
    days: int


@dataclass(frozen=True)
class Import:
    max_mb: int


@dataclass(frozen=True)
class Models:
    path: str   # models.toml; a relative path is resolved against the repo root


@dataclass(frozen=True)
class Runner:
    heartbeat_timeout_s: float
    cancel_grace_s: float


@dataclass(frozen=True)
class Stt:
    window_s: int
    vocab_hint: str    # initial_prompt used when a run asks for the vocabulary hint
    plugins: str       # comma-separated modules the worker imports to register extra engines (tests, v2.1)


@dataclass(frozen=True)
class Llm:
    single_pass_max_tokens: int   # transcripts estimated above this are split into chunks (map-reduce)
    chunk_tokens: int             # target chunk size, estimated
    chunk_overlap_tokens: int     # segments repeated at the start of the next chunk, estimated
    chars_per_token: float        # estimate, not measured (Greek tokenises worse than English)
    timeout_s: float              # one chat call
    max_tokens_debrief: int
    max_tokens_notes: int
    max_tokens_checklist: int
    max_tokens_tags: int

    def max_tokens(self, kind):
        return getattr(self, f"max_tokens_{kind}")


@dataclass(frozen=True)
class Settings:
    server: Server
    paths: Paths
    lmstudio: LmStudio
    db: Db
    record: Record
    trash: Trash
    models: Models
    runner: Runner
    stt: Stt
    llm: Llm
    import_: Import   # config section [import]; trailing underscore because `import` is a keyword


def _v_server(s):
    if not 1 <= s.port <= 65535:
        raise ValueError("server.port: must be 1-65535")


def _v_paths(s):
    pass


def _v_lmstudio(s):
    if not s.url.startswith(("http://", "https://")):
        raise ValueError("lmstudio.url: must start with http:// or https://")


def _v_db(s):
    if s.connect_timeout < 1:
        raise ValueError("db.connect_timeout: must be >= 1")


def _v_record(s):
    if s.sample_rate <= 0:
        raise ValueError("record.sample_rate: must be > 0")
    if s.sync_interval_s <= 0:
        raise ValueError("record.sync_interval_s: must be > 0")
    if s.grace_s < 0:
        raise ValueError("record.grace_s: must be >= 0")
    if s.min_free_gb < 0:
        raise ValueError("record.min_free_gb: must be >= 0")
    if s.max_msg_mb < 1:
        raise ValueError("record.max_msg_mb: must be >= 1")
    try:
        import zoneinfo
        zoneinfo.ZoneInfo(s.tz)
    except Exception:
        raise ValueError("record.tz: unknown time zone") from None


def _v_trash(s):
    if s.days < 1:
        raise ValueError("trash.days: must be >= 1")


def _v_models(s):
    if not s.path:
        raise ValueError("models.path: must not be empty")


def _v_runner(s):
    if s.heartbeat_timeout_s <= 0:
        raise ValueError("runner.heartbeat_timeout_s: must be > 0")
    if s.cancel_grace_s < 0:
        raise ValueError("runner.cancel_grace_s: must be >= 0")


def _v_import(s):
    if s.max_mb <= 0:
        raise ValueError("import.max_mb: must be > 0")


def _v_stt(s):
    if s.window_s < 1:
        raise ValueError("stt.window_s: must be >= 1")


def _v_llm(s):
    for k in ("single_pass_max_tokens", "chunk_tokens", "max_tokens_debrief", "max_tokens_notes",
              "max_tokens_checklist", "max_tokens_tags"):
        if getattr(s, k) < 1:
            raise ValueError(f"llm.{k}: must be >= 1")
    if s.chunk_overlap_tokens < 0:
        raise ValueError("llm.chunk_overlap_tokens: must be >= 0")
    if s.chars_per_token <= 0:
        raise ValueError("llm.chars_per_token: must be > 0")
    if s.timeout_s <= 0:
        raise ValueError("llm.timeout_s: must be > 0")


# section name -> (dataclass, validation hook). Later changes add a section here + a [section] in config.toml.
SECTIONS = {"server": (Server, _v_server), "paths": (Paths, _v_paths),
            "lmstudio": (LmStudio, _v_lmstudio), "db": (Db, _v_db),
            "record": (Record, _v_record), "trash": (Trash, _v_trash), "import": (Import, _v_import),
            "models": (Models, _v_models), "runner": (Runner, _v_runner),
            "stt": (Stt, _v_stt), "llm": (Llm, _v_llm)}
# extra env names per key, tried after DETAILS_<SECTION>_<KEY>: (var, transform)
ALIASES = {
    ("server", "port"): [("DETAILS_PORT", None), ("PORT", None)],
    ("paths", "hf_hub"): [("HF_HOME", lambda v: str(pathlib.Path(v).expanduser() / "hub"))],
    ("db", "url"): [("DATABASE_URL", None)],
    ("db", "url_test"): [("DATABASE_URL_TEST", None)],
    ("record", "tz"): [("DETAILS_TZ", None)],
}


def read_dotenv(path):
    """Parse KEY=VALUE lines (comments, `export`, simple quotes). Missing file -> {}."""
    out = {}
    try:
        text = pathlib.Path(path).read_text()
    except FileNotFoundError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.removeprefix("export ").split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _coerce(section, key, typ, value):
    try:
        if typ is int:
            return int(value)
        if typ is float:
            return float(value)
        if typ is bool:
            if isinstance(value, bool):
                return value
            v = str(value).strip().lower()
            if v in ("1", "true", "yes", "on"):
                return True
            if v in ("0", "false", "no", "off"):
                return False
            raise ValueError(v)
        if typ is pathlib.Path:
            return pathlib.Path(str(value)).expanduser().absolute()
        return str(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{section}.{key}: expected {typ.__name__}") from None


def load_settings(config_path=CONFIG_PATH, env=None, dotenv_path=DOTENV_PATH):
    """env defaults to os.environ; .env is merged into it WITHOUT overriding existing variables."""
    if env is None:
        env = os.environ
    if dotenv_path:
        for k, v in read_dotenv(dotenv_path).items():
            env.setdefault(k, v)
    config_path = pathlib.Path(config_path)
    try:
        raw = tomllib.loads(config_path.read_text())
    except FileNotFoundError:
        raise SettingsError(f"{config_path}: config file not found") from None
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{config_path}: invalid TOML: {e}") from None
    built = {}
    for name, (cls, validate) in SECTIONS.items():
        vals = {}
        for f in dataclasses.fields(cls):
            value = raw.get(name, {}).get(f.name)
            for var, tf in [(f"DETAILS_{name}_{f.name}".upper(), None)] + ALIASES.get((name, f.name), []):
                if env.get(var):
                    value = tf(env[var]) if tf else env[var]
                    break
            if value is None:
                raise SettingsError(f"{config_path}: missing [{name}] {f.name}")
            vals[f.name] = _coerce(name, f.name, f.type, value)
        section = cls(**vals)
        try:
            validate(section)
        except ValueError as e:
            raise SettingsError(str(e)) from None
        built["import_" if name == "import" else name] = section
    return Settings(**built)
