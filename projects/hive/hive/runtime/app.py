"""The Hive application object: settings, store, scheduler, event bus, and active runs.

The CLI and the web server both build one of these and drive runs through it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from .. import paths
from ..agents.agent import AgentSpec
from ..agents.roles import Role, list_roles
from ..config import ConfigError, Limits, Settings, load_settings
from ..scheduler import Scheduler
from ..store import Store
from .approvals import Policy
from .events import Event, EventBus
from .run import Run

log = logging.getLogger("hive.app")


def slugify(text: str, max_words: int = 6) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:max_words]
    return "-".join(words)[:48].strip("-") or "project"


def parse_member(text: str) -> dict[str, Any]:
    """``[name=]role[@backend[:model]]`` -> dict. Model names may contain ':'."""
    m = re.fullmatch(r"\s*(?:([\w-]+)=)?([\w-]+)(?:@([\w.-]+)(?::(.+))?)?\s*", text)
    if not m:
        raise ConfigError(f"can't read team member {text!r}; use role, name=role, role@backend or role@backend:model")
    name, role, backend, model = m.groups()
    return {k: v for k, v in {"name": name, "role": role, "backend": backend, "model": model}.items() if v}


class Hive:
    def __init__(self, settings: Settings, store: Store, scheduler: Scheduler):
        self.settings = settings
        self.store = store
        self.scheduler = scheduler
        self.bus = EventBus()
        self.runs: dict[str, Run] = {}
        scheduler.listeners.append(self._backend_event)

    @classmethod
    async def open(
        cls,
        settings: Settings | None = None,
        db: Path | str | None = None,
        scheduler: Scheduler | None = None,
        start_health: bool = True,
    ) -> "Hive":
        from ..tools import builtin  # noqa: F401  (registers the built-in tools)
        from ..tools.plugins import load_extra_tools

        settings = settings or load_settings()
        store = await Store(db if db is not None else paths.db_path()).open()
        scheduler = scheduler or Scheduler.from_settings(settings)
        if start_health:
            await scheduler.start()
        load_extra_tools(settings.config_dir)
        app = cls(settings, store, scheduler)
        await app._mark_interrupted()
        return app

    async def close(self) -> None:
        for run in list(self.runs.values()):
            if not run.done.is_set():
                await run.finish("interrupted", reason="Hive was shut down")
        await self.scheduler.close()
        await self.store.close()

    async def _mark_interrupted(self) -> None:
        """Runs left 'running' by a crash or kill show as interrupted."""
        for r in await self.store.query("SELECT id FROM runs WHERE status IN ('running', 'paused', 'created')"):
            await self.store.update_run(r["id"], status="interrupted")

    def _backend_event(self, kind: str, data: dict[str, Any]) -> None:
        for run in self.runs.values():
            if not run.done.is_set():
                run.spawn(run.emit(f"backend.{kind.split('_')[-1]}", **data))

    # Roles and teams -----------------------------------------------------------------------

    def roles(self) -> dict[str, Role]:
        return list_roles(self.settings.config_dir)

    def build_team(
        self,
        members: list[str | dict[str, Any]],
        backend: str | None = None,
        model: str | None = None,
    ) -> list[AgentSpec]:
        """Agent specs from role names, ``name=role@backend:model`` strings, or full dicts."""
        roles = self.roles()
        default_backend = backend or self.settings.default_backend()
        specs: list[AgentSpec] = []
        used: dict[str, int] = {}
        for m in members:
            d = parse_member(m) if isinstance(m, str) else dict(m)
            role_name = d.pop("role", None)
            if not role_name or role_name not in roles:
                raise ConfigError(f"unknown role {role_name!r} (have: {', '.join(sorted(roles))})")
            name = d.pop("name", None) or role_name
            used[name] = used.get(name, 0) + 1
            if used[name] > 1:
                name = f"{name}{used[name]}"
            b = d.pop("backend", None) or default_backend
            self.settings.backend(b)  # must exist
            mdl = d.pop("model", None) or (model if b == default_backend else None)
            specs.append(AgentSpec.from_role(roles[role_name], name=name, backend=b, model=mdl, **d))
        if not specs:
            raise ConfigError("the team is empty")
        return specs

    # Projects and runs ---------------------------------------------------------------------------

    async def create_project(self, goal: str, name: str | None = None, workspace: str | Path | None = None) -> dict[str, Any]:
        base = slugify(name or goal)
        slug, n = base, 1
        while await self.store.slug_taken(slug):
            n += 1
            slug = f"{base}-{n}"
        ws = Path(workspace).expanduser().resolve() if workspace else paths.workspaces_dir() / slug
        ws.mkdir(parents=True, exist_ok=True)
        pid = uuid.uuid4().hex[:12]
        await self.store.add_project(pid, slug, name or goal[:80], goal, str(ws))
        return await self.store.get_project(pid)  # type: ignore[return-value]

    async def start_run(
        self,
        project: dict[str, Any],
        *,
        goal: str | None = None,
        mode: str = "solo",
        team: list[AgentSpec] | None = None,
        limits: Limits | dict[str, Any] | None = None,
        approval: Policy = "ask",
    ) -> Run:
        from ..modes import MODES

        if mode not in MODES:
            raise ConfigError(f"unknown mode {mode!r} (have: {', '.join(MODES)})")
        if team is None:
            team = self.build_team(list(MODES[mode].default_team))
        if isinstance(limits, dict):
            limits = self.settings.hive.limits.model_copy(update=limits)
        limits = limits or self.settings.hive.limits.model_copy()
        for spec in team:  # learn tool support and real context sizes before the first call
            try:
                await asyncio.wait_for(self.scheduler.backend(spec.backend).probe(spec.model), 10)
            except Exception as e:
                log.info("probe of %s failed: %s", spec.backend, e)
        run = Run(self, uuid.uuid4().hex[:12], project, goal or project["goal"], mode, team, limits, approval)
        self.runs[run.id] = run
        await run.start()
        return run

    def subscribe(self, run_id: str | None = None):
        return self.bus.subscribe(run_id)

    async def events(self, run_id: str, after: int = 0) -> list[Event]:
        rows = await self.store.events(run_id, after)
        return [Event(run_id, r["seq"], r["ts"], r["kind"], r["agent"], r["payload"]) for r in rows]
