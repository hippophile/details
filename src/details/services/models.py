"""Model registry loaded from models.toml: [stt.<id>], [llm.<id>] and [diar.<id>] blocks, validated at load time."""
import importlib.util, pathlib, tomllib
from dataclasses import dataclass, field

from ..settings import REPO_ROOT

ENGINES = {"stt": "mlx_whisper", "llm": "lmstudio", "diar": "pyannote"}
ROLES = ("cheap", "nice", "fast", "accurate")
# required fields per kind -> type(s)
REQUIRED = {"stt": {"engine": str, "label": str, "repo": str, "needs_gb": (int, float), "role": str},
            "llm": {"engine": str, "label": str, "model": str, "context": int, "needs_gb": (int, float),
                    "role": str},
            "diar": {"engine": str, "label": str, "repo": str, "needs_gb": (int, float), "role": str}}


class ModelsConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SttModel:
    id: str
    engine: str
    label: str
    repo: str
    needs_gb: float
    role: str
    langs: tuple = ()
    options: dict = field(default_factory=dict)
    enabled: bool = True
    kind: str = "stt"


@dataclass(frozen=True)
class LlmModel:
    id: str
    engine: str
    label: str
    model: str          # LM Studio model key
    context: int
    needs_gb: float
    role: str
    kind: str = "llm"


@dataclass(frozen=True)
class DiarModel:
    id: str
    engine: str
    label: str
    repo: str           # Hugging Face repo of the pipeline snapshot
    needs_gb: float
    role: str
    kind: str = "diar"


@dataclass(frozen=True)
class Registry:
    stt: dict
    llm: dict
    diar: dict = field(default_factory=dict)

    def get(self, model_id):
        return self.stt.get(model_id) or self.llm.get(model_id)


def resolve_path(settings):
    p = pathlib.Path(settings.models.path).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


def load_registry(path):
    path = pathlib.Path(path)
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ModelsConfigError(f"{path}: model registry not found") from None
    except tomllib.TOMLDecodeError as e:
        raise ModelsConfigError(f"{path}: invalid TOML: {e}") from None
    out = {"stt": {}, "llm": {}, "diar": {}}
    for kind in ("stt", "llm", "diar"):
        table = raw.get(kind, {})
        if not isinstance(table, dict):
            raise ModelsConfigError(f"{path}: [{kind}] must contain [{kind}.<id>] tables")
        for mid, block in table.items():
            name = f"{kind}.{mid}"
            if not isinstance(block, dict):
                raise ModelsConfigError(f"{path}: {name} must be a table")
            out[kind][mid] = _build(path, kind, mid, name, block)
    return Registry(stt=out["stt"], llm=out["llm"], diar=out["diar"])


def _build(path, kind, mid, name, b):
    for key, typ in REQUIRED[kind].items():
        if key not in b:
            raise ModelsConfigError(f"{path}: {name}: missing field '{key}'")
        if isinstance(b[key], bool) or not isinstance(b[key], typ):
            raise ModelsConfigError(f"{path}: {name}: field '{key}' has the wrong type")
    if b["engine"] != ENGINES[kind]:
        raise ModelsConfigError(f"{path}: {name}: engine '{b['engine']}' is not allowed for {kind}; "
                                f"allowed engine ids: {ENGINES[kind]}")
    if b["role"] not in ROLES:
        raise ModelsConfigError(f"{path}: {name}: role '{b['role']}' must be one of {', '.join(ROLES)}")
    if kind == "stt":
        return SttModel(id=mid, engine=b["engine"], label=b["label"], repo=b["repo"], needs_gb=b["needs_gb"],
                        role=b["role"], langs=tuple(b.get("langs", [])), options=dict(b.get("options", {})),
                        enabled=bool(b.get("enabled", True)))
    if kind == "diar":
        return DiarModel(id=mid, engine=b["engine"], label=b["label"], repo=b["repo"], needs_gb=b["needs_gb"],
                         role=b["role"])
    return LlmModel(id=mid, engine=b["engine"], label=b["label"], model=b["model"], context=b["context"],
                    needs_gb=b["needs_gb"], role=b["role"])


# ---- installed check + API payload ---------------------------------------------------------------------

def stt_installed(hub, repo):
    """Weights present in the Hugging Face hub cache (same rule as v1 `models()`)."""
    d = pathlib.Path(hub) / ("models--" + repo.replace("/", "--"))
    return any((d / "snapshots").glob("*/weights.*"))


WEIGHT_SUFFIXES = (".bin", ".safetensors", ".ckpt")


def pyannote_importable():
    """True when pyannote.audio can be imported. Never raises and never imports it: find_spec("pyannote.audio") RAISES
    ModuleNotFoundError when the parent package `pyannote` is absent, so the parent is checked first."""
    try:
        return importlib.util.find_spec("pyannote") is not None and importlib.util.find_spec("pyannote.audio") is not None
    except (ModuleNotFoundError, ValueError, ImportError):
        return False


def _snapshots(hub, repo):
    d = pathlib.Path(hub) / ("models--" + repo.replace("/", "--")) / "snapshots"
    return [p for p in d.glob("*") if p.is_dir()] if d.is_dir() else []


def _has_config(hub, repo):
    return any((p / "config.yaml").is_file() for p in _snapshots(hub, repo))


def model_cached(hub, repo):
    """A complete snapshot in the Hugging Face cache: config.yaml AND at least one weight file (*.bin, *.safetensors,
    *.ckpt) in the same snapshot. The weight-file rule is checked against the real download (speakers-diarization 9.2)."""
    return any((p / "config.yaml").is_file() and any(f.is_file() and f.suffix in WEIGHT_SUFFIXES for f in p.rglob("*"))
               for p in _snapshots(hub, repo))


def diar_status(entry, settings):
    """-> (installed, reason, hint) for one diarization entry. No torch import, never raises."""
    if not pyannote_importable():
        return False, "pyannote.audio is not installed", "run `make setup-diar`"
    hub = settings.paths.hf_hub
    if model_cached(hub, entry.repo):
        return True, None, None          # a cached model works offline: no token needed at run time
    if _has_config(hub, entry.repo):
        return False, f"the download of {entry.repo} is incomplete", "run `make diar-model` again"
    if settings.diarization.hf_token:
        return (False, f"the speaker model {entry.repo} is not downloaded",
                f"accept the model terms on https://huggingface.co/{entry.repo}, then run `make diar-model`")
    return (False, f"the speaker model {entry.repo} is not downloaded",
            f"create a free Hugging Face token, accept the model terms on https://huggingface.co/{entry.repo}, "
            "put HF_TOKEN=... in .env, then run `make diar-model`")


async def describe(registry, settings, ctl):
    """-> {stt:[...], llm:[...], diar:[...], lmstudio:{reachable}}; never raises because LM Studio is down."""
    reachable = await ctl.reachable()
    downloaded = None
    if reachable:
        try:
            downloaded = {m.key for m in await ctl.list_downloaded()}
        except Exception:
            reachable = False
    stt = []
    for e in registry.stt.values():
        item = _item(e)
        if not e.enabled:
            item.update(installed=False, unavailable_reason="disabled in models.toml")
        elif stt_installed(settings.paths.hf_hub, e.repo):
            item["installed"] = True
        else:
            item.update(installed=False, unavailable_reason=f"weights for {e.repo} are not in the Hugging Face cache",
                        hint="run `make setup` or download the repo with huggingface_hub")
        stt.append(item)
    llm = []
    for e in registry.llm.values():
        item = _item(e)
        if downloaded is not None and e.model in downloaded:
            item["installed"] = True
        elif downloaded is None:
            item.update(installed=False, unavailable_reason="LM Studio is not reachable",
                        hint="start LM Studio (and its local server)")
        else:
            item.update(installed=False, unavailable_reason="model is not downloaded in LM Studio",
                        hint=f"download '{e.model}' in LM Studio")
        llm.append(item)
    diar = []
    for e in registry.diar.values():
        item = _item(e)
        ok, reason, hint = diar_status(e, settings)
        if ok:
            item["installed"] = True
        else:
            item.update(installed=False, unavailable_reason=reason, hint=hint)
        diar.append(item)
    return {"stt": stt, "llm": llm, "diar": diar, "lmstudio": {"reachable": reachable}}


def _item(e):
    return {"id": e.id, "kind": e.kind, "label": e.label, "engine": e.engine, "role": e.role,
            "needs_gb": e.needs_gb}
