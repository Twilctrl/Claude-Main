"""Notification hooks: run a command and/or POST to a URL when a run needs you or finishes.

Configured in hive.yaml::

    notify:
      events: [finished, approval, attention]
      command: notify-send Hive "{message}"      # or: espeak "{message}", or a Piper script
      url: http://192.168.1.10:8123/api/webhook/hive   # JSON POST (Home Assistant, ntfy, ...)

The command is split into arguments and run **without a shell**, with
``{message}``, ``{run}``, ``{status}``, ``{kind}`` and ``{goal}`` filled in
per argument. Messages can contain model output (an approval request quotes
the command an agent wants to run), and no shell means that text can't
inject commands.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
from typing import TYPE_CHECKING, Any

import httpx

from .config import NotifyConfig
from .context import clip

if TYPE_CHECKING:
    from .runtime.app import Hive
    from .runtime.events import Event

log = logging.getLogger("hive.notify")

TRIGGERS = {"run.finished": "finished", "approval.requested": "approval", "run.attention": "attention"}


def describe(ev: Event, goal: str) -> dict[str, str]:
    """The fields a notification can use."""
    p = ev.payload
    if ev.kind == "run.finished":
        status = p.get("status", "")
        msg = f"Hive run {status.replace('_', ' ')}: {clip(goal, 80)}"
        if p.get("reason"):
            msg += f" ({clip(p['reason'], 120)})"
    elif ev.kind == "approval.requested":
        status = "waiting"
        detail = p.get("args", {}).get("command") or p.get("args", {}).get("path") or ""
        msg = f"Hive: {ev.agent} wants approval for {p.get('tool')}: {clip(str(detail), 120)}"
    else:
        status = "attention"
        msg = f"Hive needs you: {clip(p.get('message', ''), 160)}"
    return {"message": msg, "run": ev.run_id, "status": status, "kind": TRIGGERS.get(ev.kind, ev.kind), "goal": clip(goal, 200)}


def build_argv(command: str, fields: dict[str, str]) -> list[str]:
    if sys.platform == "win32":
        # Non-POSIX splitting keeps backslashes in paths but also keeps the quotes: drop them.
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in shlex.split(command, posix=False)]
    else:
        parts = shlex.split(command)
    out = []
    for part in parts:
        for k, v in fields.items():
            part = part.replace("{" + k + "}", v)
        out.append(part)
    return out


class Notifier:
    def __init__(self, app: Hive, config: NotifyConfig):
        self.app = app
        self.config = config
        self._tasks: set[asyncio.Task[Any]] = set()

    def __call__(self, ev: Event) -> None:
        kind = TRIGGERS.get(ev.kind)
        if kind is None or kind not in self.config.events:
            return
        run = self.app.runs.get(ev.run_id)
        fields = describe(ev, run.goal if run else "")
        t = asyncio.create_task(self.send(fields))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def send(self, fields: dict[str, str]) -> None:
        if self.config.command:
            try:
                argv = build_argv(self.config.command, fields)
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
                )
                _, err = await asyncio.wait_for(proc.communicate(), self.config.timeout)
                if proc.returncode:
                    log.warning("notify command exited %s: %s", proc.returncode, err.decode(errors="replace")[:200])
            except Exception as e:  # a broken hook must never disturb a run
                log.warning("notify command failed: %s", e)
        if self.config.url:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    await client.post(self.config.url, json=fields)
            except Exception as e:
                log.warning("notify webhook failed: %s", e)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=self.config.timeout + 1)
