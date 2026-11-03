"""MemoryManager: keeps LM Studio and the speech step from fighting over RAM. Only models this app loaded
(identifier starts with `details-`) are unloaded, unless settings.lmstudio.exclusive is true.
Loaded state is read from LM Studio each time, so a restarted server still recognises its own models."""
import logging, re, subprocess
from typing import Protocol

from ..llm.lmstudio_ctl import APP_PREFIX, LMStudioError, identifier_for

log = logging.getLogger("details.memory")


class MemoryShort(RuntimeError):
    pass


class MemInfo(Protocol):
    def free_gb(self) -> float: ...


class VmStatMemInfo:
    """Free + inactive memory from macOS `vm_stat`. If it cannot be read, reports unlimited (no preflight)."""

    def free_gb(self):
        try:
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5, check=True).stdout
            page = int(re.search(r"page size of (\d+) bytes", out).group(1))
            pages = sum(int(re.search(rf"Pages {k}:\s+(\d+)", out).group(1)) for k in ("free", "inactive"))
            return pages * page / 1024 ** 3
        except Exception:
            log.warning("cannot read vm_stat; memory preflight disabled")
            return float("inf")


class MemoryManager:
    def __init__(self, ctl, registry, settings, meminfo):
        self.ctl, self.registry, self.settings, self.meminfo = ctl, registry, settings, meminfo

    def _mine(self, loaded):
        return self.settings.lmstudio.exclusive or loaded.identifier.startswith(APP_PREFIX)

    async def owned(self):
        return [m for m in await self.ctl.list_loaded() if self._mine(m)]

    async def release(self, keep=None):
        """Unload every model this app owns (except identifier `keep`)."""
        for m in await self.owned():
            if m.identifier != keep:
                await self.ctl.unload(m.identifier)

    async def release_for_stt(self):
        """Speech steps call this before spawning. LM Studio being down means nothing to release."""
        try:
            await self.release()
        except LMStudioError as e:
            log.warning("release_for_stt: %s", e)

    async def ensure_llm(self, entry):
        """Unload other app-loaded models, check memory, load `entry` as details-<id>."""
        ident = identifier_for(entry.id)
        loaded = await self.ctl.list_loaded()
        await self.release(keep=ident)
        if any(m.identifier == ident for m in loaded):
            return
        free = self.meminfo.free_gb()
        if free < entry.needs_gb:
            raise MemoryShort(f"{entry.id} needs ~{entry.needs_gb:g} GB, {free:g} GB free")
        await self.ctl.load(entry.model, ident, entry.context)
