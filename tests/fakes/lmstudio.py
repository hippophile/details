"""FakeLMStudio (a Transport that records calls and answers from recorded `lms` JSON) and FakeMemInfo."""
import copy, json, pathlib

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


class FakeLMStudio:
    """Implements the llm.lmstudio_ctl.Transport protocol. `calls` = [("unload", id), ("load", key, id, ctx), ...].
    Loaded state follows load/unload. Pass loaded=[{"identifier":..., "modelKey":...}] to preload models."""

    def __init__(self, ls=None, loaded=None, reachable=True):
        self.ls = copy.deepcopy(ls if ls is not None else fixture("lms_ls.json"))
        template = fixture("lms_ps.json")[0]
        self._template = template
        self.loaded = [{**template, **m} for m in (loaded if loaded is not None else [])]
        self.up = reachable
        self.calls = []

    def _need_up(self):
        if not self.up:
            from details.llm.lmstudio_ctl import LMStudioError
            raise LMStudioError("LM Studio is not reachable")

    async def reachable(self):
        return self.up

    async def list_downloaded(self):
        self._need_up()
        return copy.deepcopy(self.ls)

    async def list_loaded(self):
        self._need_up()
        return copy.deepcopy(self.loaded)

    async def load(self, model_key, identifier, context):
        self._need_up()
        self.calls.append(("load", model_key, identifier, context))
        self.loaded.append({**self._template, "modelKey": model_key, "identifier": identifier,
                            "contextLength": context})

    async def unload(self, identifier):
        self._need_up()
        self.calls.append(("unload", identifier))
        self.loaded = [m for m in self.loaded if m["identifier"] != identifier]

    # extension point: llm-outputs adds an HTTP mode (a local aiohttp server speaking /v1) here
    async def start_http(self):
        raise NotImplementedError("HTTP mode is added by llm-outputs")


class FakeMemInfo:
    def __init__(self, free_gb=64.0):
        self.free = free_gb

    def free_gb(self):
        return self.free
