"""The ``run_shell`` tool: off unless an agent lists it, and it asks before every command.

What it does to limit damage (DESIGN.md §10):

- the working directory is the workspace; stdin is closed;
- the environment is scrubbed (no API keys or tokens) and HOME points to a
  scratch folder outside the workspace;
- a timeout kills the whole process tree; output is capped, keeping the head
  and the tail;
- on Linux, if bubblewrap (``bwrap``) is installed and works, the command runs
  inside it: the filesystem is read-only except the workspace and the scratch
  folder, and there is no network.

**Without bwrap this is not a security boundary.** A command can read your
files and reach the network. That's why it's off by default and needs approval.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from . import ToolContext, ToolError, ToolResult, tool

_SHELL_META = re.compile(r"[;&|`$<>\n]|\$\(")
_KEEP_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR", "PYTHONIOENCODING")
_bwrap_ok: bool | None = None


@dataclass
class ShellResult:
    exit_code: int | None
    output: str
    timed_out: bool
    sandbox: str  # "bwrap" or "none"


def scrubbed_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in _KEEP_ENV}
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["TMPDIR"] = env["TEMP"] = env["TMP"] = str(home)
    env.setdefault("PATH", os.defpath)
    return env


def _bwrap_works(bwrap: str) -> bool:
    """bwrap needs unprivileged user namespaces, which some systems and containers block."""
    global _bwrap_ok
    if _bwrap_ok is None:
        try:
            r = subprocess.run(
                [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-all", "--die-with-parent", "true"],
                capture_output=True,
                timeout=10,
            )
            _bwrap_ok = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _bwrap_ok = False
    return _bwrap_ok


def build_argv(command: str, cwd: Path, scratch: Path, sandbox: str) -> tuple[list[str], str]:
    if sys.platform == "win32":
        if sandbox == "bwrap":
            raise ToolError("shell.sandbox is 'bwrap', which isn't available on Windows")
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command], "none"
    shell = shutil.which("bash") or "/bin/sh"
    bwrap = shutil.which("bwrap") if sandbox in ("auto", "bwrap") else None
    if bwrap and _bwrap_works(bwrap):
        argv = [
            bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
            "--bind", str(cwd), str(cwd), "--bind", str(scratch), str(scratch),
            "--unshare-all", "--die-with-parent", "--new-session", "--chdir", str(cwd),
            shell, "-c", command,
        ]
        return argv, "bwrap"
    if sandbox == "bwrap":
        raise ToolError("shell.sandbox is 'bwrap' but bubblewrap isn't installed or can't create namespaces here")
    return [shell, "-c", command], "none"


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def run_command(command: str, cwd: Path, *, timeout: float, max_output: int, sandbox: str, scratch: Path) -> ShellResult:
    """Run ``command`` with the restrictions above. Never raises on a failing command."""
    scratch.mkdir(parents=True, exist_ok=True)
    argv, used = build_argv(command, cwd, scratch, sandbox)
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), env=scrubbed_env(scratch), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kwargs,
    )
    half = max(1000, max_output // 2)
    head = bytearray()
    tail: deque[bytes] = deque()
    tail_len = 0
    total = 0

    async def pump() -> None:
        nonlocal tail_len, total
        assert proc.stdout is not None
        while chunk := await proc.stdout.read(8192):
            total += len(chunk)
            if len(head) < half:
                take = chunk[: half - len(head)]
                head.extend(take)
                chunk = chunk[len(take):]
            if chunk:
                tail.append(chunk)
                tail_len += len(chunk)
                while tail_len - len(tail[0]) >= half:
                    tail_len -= len(tail.popleft())

    timed_out = False
    try:
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout)
    except asyncio.TimeoutError:
        timed_out = True
        _kill_tree(proc)
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            pass
    except asyncio.CancelledError:
        _kill_tree(proc)
        raise
    out = head.decode("utf-8", "replace")
    rest = b"".join(tail)
    if total > len(head) + len(rest):
        out += f"\n[... {total - len(head) - len(rest)} bytes cut ...]\n"
    out += rest.decode("utf-8", "replace")
    return ShellResult(proc.returncode, out, timed_out, used)


def _risky(ctx: ToolContext, args: dict) -> bool:
    """Ask unless the command starts with an allow-listed prefix and has no shell operators."""
    cmd = str(args.get("command", "")).strip()
    if _SHELL_META.search(cmd):
        return True
    allow = ctx.run.app.settings.hive.shell.allow
    return not any(cmd == p or cmd.startswith(p.rstrip() + " ") for p in allow)


@tool(group="shell", risky=_risky)
async def run_shell(ctx: ToolContext, command: str, timeout: int = 0) -> ToolResult:
    """Run a shell command in the project folder (for example tests) and get its output. Needs approval.

    Args:
        command: the command line, e.g. "python -m pytest -q"
        timeout: seconds before it's killed (0 = the configured default)
    """
    cfg = ctx.run.app.settings.hive.shell
    limit = min(timeout, cfg.timeout) if timeout > 0 else cfg.timeout
    scratch = paths.data_dir() / "scratch" / ctx.run.id
    res = await run_command(command, ctx.run.workspace.root, timeout=limit, max_output=cfg.max_output, sandbox=cfg.sandbox, scratch=scratch)
    changed = await ctx.run.files.scan(f"{ctx.agent.name} (shell)")
    status = "timed out" if res.timed_out else f"exit code {res.exit_code}"
    note = "" if res.sandbox == "bwrap" else " (not sandboxed)"
    files = f"\nFiles changed: {', '.join(changed)}" if changed else ""
    return ToolResult(
        f"$ {command}\n[{status}{note}]\n{res.output.rstrip()}{files}",
        ok=not res.timed_out and res.exit_code == 0,
        data={"exit_code": res.exit_code, "timed_out": res.timed_out, "sandbox": res.sandbox, "changed": changed},
        progress=bool(changed),
    )
