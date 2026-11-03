"""LM Studio controller. Transport decision (spike 2026-11-03, installed lms, CLI commit 43d4b26):
the `lms` CLI with --json is used for list/load/unload. The REST API (/api/v0/models) has no load/unload and
reports loaded instances by *identifier* but downloaded models by *model key* in the same `id` field, so it cannot
tell the two apart. REST is used only as the cheap reachability probe (settings.lmstudio.url).
`lms ls --json` -> [{type, modelKey, displayName, path, ...}]
`lms ps --json` -> [{type, modelKey, identifier, contextLength, status, ...}]
The controller depends on the small Transport protocol so fakes (and a later HTTP transport) fit."""
import asyncio, pathlib, urllib.request
from dataclasses import dataclass
from typing import Protocol

APP_PREFIX = "details-"   # identifiers of models loaded by this app


class LMStudioError(RuntimeError):
    pass


def identifier_for(model_id):
    return APP_PREFIX + model_id


@dataclass(frozen=True)
class Downloaded:
    key: str
    type: str
    display_name: str


@dataclass(frozen=True)
class Loaded:
    identifier: str
    model_key: str
    context: int | None


class Transport(Protocol):
    async def reachable(self) -> bool: ...
    async def list_downloaded(self) -> list[dict]: ...
    async def list_loaded(self) -> list[dict]: ...
    async def load(self, model_key: str, identifier: str, context: int) -> None: ...
    async def unload(self, identifier: str) -> None: ...


class CliTransport:
    """`lms` subprocess transport. `run(argv) -> stdout text` is injectable for tests."""

    def __init__(self, settings, run=None):
        self.cli = str(pathlib.Path(settings.lmstudio.cli).expanduser())
        self.url = settings.lmstudio.url.rstrip("/")
        self._run = run or self._exec

    async def _exec(self, argv, timeout=300):
        try:
            p = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            raise LMStudioError(f"cannot run {argv[0]}: {e.strerror or e}") from None
        try:
            out, err = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()
            raise LMStudioError(f"{argv[0]} {argv[1]} timed out after {timeout}s") from None
        if p.returncode != 0:
            tail = err.decode(errors="replace").strip().splitlines()[-1:] or [""]
            raise LMStudioError(f"{argv[0]} {argv[1]} failed (exit {p.returncode}): {tail[0]}")
        return out.decode(errors="replace")

    async def _json(self, *args):
        import json
        text = await self._run([self.cli, *args])
        try:
            return json.loads(text)
        except ValueError:
            raise LMStudioError(f"{self.cli} {args[0]}: output is not JSON") from None

    async def reachable(self):
        def probe():
            try:
                with urllib.request.urlopen(self.url + "/api/v0/models", timeout=2) as r:
                    return r.status == 200
            except Exception:
                return False
        return await asyncio.to_thread(probe)

    async def list_downloaded(self):
        return await self._json("ls", "--json")

    async def list_loaded(self):
        return await self._json("ps", "--json")

    async def load(self, model_key, identifier, context):
        await self._run([self.cli, "load", model_key, "--identifier", identifier, "-c", str(context), "-y"])

    async def unload(self, identifier):
        await self._run([self.cli, "unload", identifier])


class Controller:
    def __init__(self, transport):
        self.transport = transport

    async def reachable(self):
        return await self.transport.reachable()

    async def list_downloaded(self):
        return [Downloaded(m["modelKey"], m.get("type", ""), m.get("displayName", m["modelKey"]))
                for m in await self.transport.list_downloaded()]

    async def list_loaded(self):
        return [Loaded(m["identifier"], m["modelKey"], m.get("contextLength"))
                for m in await self.transport.list_loaded()]

    async def load(self, model_key, identifier, context):
        await self.transport.load(model_key, identifier, context)

    async def unload(self, identifier):
        await self.transport.unload(identifier)
