"""Child-process helper: run a subprocess, treat each stdout line as a heartbeat, kill it on silence or cancel.
Protocol for children: print one stdout line at model load and at least one per window."""
import asyncio, os, signal
from dataclasses import dataclass

from .steps import StepError


@dataclass
class ChildResult:
    returncode: int | None
    lines: list


def _signal(proc, sig):
    try:
        os.killpg(proc.pid, sig)        # the child runs in its own session: take grandchildren too
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            pass


async def terminate(proc, grace):
    """SIGTERM, then SIGKILL after `grace` seconds."""
    if proc.returncode is not None:
        return
    _signal(proc, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), grace)
    except asyncio.TimeoutError:
        _signal(proc, signal.SIGKILL)
        await proc.wait()


class ChildGroup:
    """The live children of one running job, so cancel can kill them."""

    def __init__(self):
        self.procs = set()

    async def kill_all(self, grace):
        await asyncio.gather(*(terminate(p, grace) for p in list(self.procs)))


async def run_child(argv, *, heartbeat_timeout, grace, cancelled, group, on_line=None, stderr_path=None):
    if cancelled.is_set():
        return ChildResult(None, [])
    stderr = open(stderr_path, "ab") if stderr_path else asyncio.subprocess.DEVNULL
    try:
        proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=stderr,
                                                    start_new_session=True, limit=1 << 20)
    except OSError as e:
        raise StepError(f"cannot start {argv[0]}: {e.strerror or e}") from None
    finally:
        if stderr_path:
            stderr.close()
    group.procs.add(proc)
    lines, hung = [], False
    try:
        if cancelled.is_set():
            await terminate(proc, grace)
        while True:
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), heartbeat_timeout)
            except asyncio.TimeoutError:
                hung = True
                break
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip("\r\n")
            lines.append(line)
            if on_line:
                on_line(line)
        if not hung:
            try:
                await asyncio.wait_for(proc.wait(), heartbeat_timeout)
            except asyncio.TimeoutError:
                hung = True
        if hung and not cancelled.is_set():
            await terminate(proc, grace)
            raise StepError(f"no heartbeat for {heartbeat_timeout:g}s (child killed)")
        if hung:
            await terminate(proc, grace)
    finally:
        if proc.returncode is None:
            _signal(proc, signal.SIGKILL)
            await proc.wait()
        group.procs.discard(proc)
    if proc.returncode != 0 and not cancelled.is_set():
        raise StepError(f"child exited with code {proc.returncode}")
    return ChildResult(proc.returncode, lines)
