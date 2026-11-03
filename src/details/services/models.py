"""Model registry loaded from models.toml: [stt.<id>] and [llm.<id>] blocks, validated at load time."""
import pathlib, tomllib
from dataclasses import dataclass, field

from ..settings import REPO_ROOT

ENGINES = {"stt": "mlx_whisper", "llm": "lmstudio"}
ROLES = ("cheap", "nice", "fast", "accurate")
# required fields per kind -> type(s)
REQUIRED = {"stt": {"engine": str, "label": str, "repo": str, "needs_gb": (int, float), "role": str},
            "llm": {"engine": str, "label": str, "model": str, "context": int, "needs_gb": (int, float),
                    "role": str}}


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
class Registry:
    stt: dict
    llm: dict

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
    out = {"stt": {}, "llm": {}}
    for kind in ("stt", "llm"):
        table = raw.get(kind, {})
        if not isinstance(table, dict):
            raise ModelsConfigError(f"{path}: [{kind}] must contain [{kind}.<id>] tables")
        for mid, block in table.items():
            name = f"{kind}.{mid}"
            if not isinstance(block, dict):
                raise ModelsConfigError(f"{path}: {name} must be a table")
            out[kind][mid] = _build(path, kind, mid, name, block)
    return Registry(stt=out["stt"], llm=out["llm"])


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
    return LlmModel(id=mid, engine=b["engine"], label=b["label"], model=b["model"], context=b["context"],
                    needs_gb=b["needs_gb"], role=b["role"])


# ---- installed check + API payload ---------------------------------------------------------------------

def stt_installed(hub, repo):
    """Weights present in the Hugging Face hub cache (same rule as v1 `models()`)."""
    d = pathlib.Path(hub) / ("models--" + repo.replace("/", "--"))
    return any((d / "snapshots").glob("*/weights.*"))


async def describe(registry, settings, ctl):
    """-> {stt:[...], llm:[...], lmstudio:{reachable}}; never raises because LM Studio is down."""
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
    return {"stt": stt, "llm": llm, "lmstudio": {"reachable": reachable}}


def _item(e):
    return {"id": e.id, "kind": e.kind, "label": e.label, "engine": e.engine, "role": e.role,
            "needs_gb": e.needs_gb}
