"""The blackboard and the task board: shared state every agent can read and write (DESIGN.md §8)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from ..store import Store
from .events import Event

Emit = Callable[..., Awaitable[Event]]

BOARD_KINDS = ("note", "decision", "artifact", "deliverable")
TASK_STATUSES = ("todo", "in_progress", "review", "done", "failed")


class BoardError(Exception):
    """A board or task operation the agent got wrong. The message is shown to it."""


# --- blackboard -------------------------------------------------------------------------


@dataclass
class BoardEntry:
    id: int
    kind: str
    title: str
    content: str
    author: str
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Blackboard:
    def __init__(self, store: Store, run_id: str, emit: Emit):
        self.store = store
        self.run_id = run_id
        self.emit = emit
        self.entries: list[BoardEntry] = []

    async def add(self, kind: str, title: str, content: str, author: str) -> BoardEntry:
        if kind not in BOARD_KINDS:
            raise BoardError(f"unknown board entry kind {kind!r}")
        if not (title or content).strip():
            raise BoardError("a board entry needs a title or content")
        entry = BoardEntry(len(self.entries) + 1, kind, title.strip()[:200], content, author)
        self.entries.append(entry)
        await self.store.upsert_board(self.run_id, entry.id, entry.to_dict())
        await self.emit("board.entry", author, entry=entry.to_dict())
        return entry

    def find(self, kind: str = "", query: str = "", limit: int = 20) -> list[BoardEntry]:
        q = query.lower().strip()
        hits = [
            e for e in self.entries
            if (not kind or e.kind == kind) and (not q or q in e.title.lower() or q in e.content.lower())
        ]
        return hits[-limit:]

    def latest(self, kind: str) -> BoardEntry | None:
        hits = [e for e in self.entries if e.kind == kind]
        return hits[-1] if hits else None

    def load(self, rows: list[dict[str, Any]]) -> None:
        self.entries = [BoardEntry(**r) for r in rows]


# --- task board -------------------------------------------------------------------------------


@dataclass
class Task:
    id: int
    title: str
    description: str = ""
    status: str = "todo"
    owner: str | None = None
    depends_on: list[int] = field(default_factory=list)
    result: str = ""
    created_by: str = ""
    role: str | None = None  # suggested role, for workers claiming from the board
    notes: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def line(self) -> str:
        deps = f" (after #{', #'.join(map(str, self.depends_on))})" if self.depends_on else ""
        owner = f" @{self.owner}" if self.owner else ""
        return f"#{self.id} [{self.status}]{owner} {self.title}{deps}"


AssignHook = Callable[[Task, str], Awaitable[None]]


class TaskBoard:
    def __init__(self, store: Store, run_id: str, emit: Emit):
        self.store = store
        self.run_id = run_id
        self.emit = emit
        self.tasks: dict[int, Task] = {}
        self.on_assigned: AssignHook | None = None  # the Run delivers the task to the owner's inbox

    def get(self, task_id: int) -> Task:
        try:
            return self.tasks[int(task_id)]
        except (KeyError, ValueError, TypeError):
            have = ", ".join(f"#{i}" for i in self.tasks) or "none yet"
            raise BoardError(f"no task #{task_id} (tasks: {have})") from None

    def deps_done(self, t: Task) -> bool:
        return all(self.tasks[d].status == "done" for d in t.depends_on if d in self.tasks)

    def ready(self) -> list[Task]:
        """Tasks waiting to be worked on whose dependencies are all done."""
        return [t for t in self.tasks.values() if t.status == "todo" and self.deps_done(t)]

    def open(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status not in ("done", "failed")]

    def all_done(self) -> bool:
        return bool(self.tasks) and all(t.status in ("done", "failed") for t in self.tasks.values())

    async def _save(self, t: Task, actor: str, change: str) -> None:
        t.updated_at = time.time()
        await self.store.upsert_task(self.run_id, t.id, t.to_dict())
        await self.emit("task.updated", actor, change=change, task=t.to_dict())

    async def create(
        self,
        title: str,
        description: str = "",
        *,
        created_by: str,
        depends_on: list[int] | None = None,
        assign_to: str | None = None,
        role: str | None = None,
    ) -> Task:
        if not title.strip():
            raise BoardError("a task needs a title")
        deps = []
        for d in depends_on or []:
            self.get(d)  # must exist (so dependencies can't form a cycle)
            deps.append(int(d))
        t = Task(max(self.tasks, default=0) + 1, title.strip()[:200], description.strip(), depends_on=deps,
                 created_by=created_by, role=role)
        self.tasks[t.id] = t
        await self._save(t, created_by, "created")
        if assign_to:
            await self.assign(t.id, assign_to, by=created_by)
        return t

    async def assign(self, task_id: int, agent: str, *, by: str) -> Task:
        t = self.get(task_id)
        if t.status in ("done", "failed"):
            raise BoardError(f"task #{t.id} is already {t.status}")
        t.owner = agent
        await self._save(t, by, f"assigned to {agent}")
        if self.on_assigned and self.deps_done(t):
            await self.on_assigned(t, by)
        return t

    async def claim(self, task_id: int, agent: str) -> Task:
        t = self.get(task_id)
        if t.owner and t.owner != agent:
            raise BoardError(f"task #{t.id} belongs to {t.owner}")
        if t.status != "todo":
            raise BoardError(f"task #{t.id} is {t.status}, not todo")
        if not self.deps_done(t):
            waiting = [f"#{d}" for d in t.depends_on if self.tasks[d].status != "done"]
            raise BoardError(f"task #{t.id} is waiting for {', '.join(waiting)}")
        t.owner, t.status = agent, "in_progress"
        await self._save(t, agent, "claimed")
        return t

    async def start(self, task_id: int, agent: str) -> Task:
        t = self.get(task_id)
        if t.status == "todo":
            t.status = "in_progress"
            t.owner = t.owner or agent
            await self._save(t, agent, "started")
        return t

    async def complete(self, task_id: int, agent: str, result: str) -> Task:
        t = self.get(task_id)
        if t.owner and t.owner != agent and agent not in ("user", "system"):
            raise BoardError(f"task #{t.id} belongs to {t.owner}; only they can complete it")
        if t.status == "done":
            raise BoardError(f"task #{t.id} is already done")
        t.status, t.result = "done", result.strip()
        t.owner = t.owner or agent
        await self._save(t, agent, "done")
        # Tasks that were waiting only on this one can now go to their owners.
        if self.on_assigned:
            for other in self.tasks.values():
                if other.owner and other.status == "todo" and t.id in other.depends_on and self.deps_done(other):
                    await self.on_assigned(other, "system")
        return t

    async def fail(self, task_id: int, agent: str, reason: str) -> Task:
        t = self.get(task_id)
        if t.status in ("done", "failed"):
            raise BoardError(f"task #{t.id} is already {t.status}")
        t.status = "failed"
        t.notes.append(f"failed by {agent}: {reason}")
        await self._save(t, agent, "failed")
        return t

    async def handoff(self, task_id: int, agent: str, to: str, note: str) -> Task:
        t = self.get(task_id)
        if t.owner and t.owner != agent:
            raise BoardError(f"task #{t.id} belongs to {t.owner}")
        t.status = "todo"
        t.owner = to
        if note:
            t.notes.append(f"{agent} → {to}: {note}")
        await self._save(t, agent, f"handed off to {to}")
        if self.on_assigned and self.deps_done(t):
            await self.on_assigned(t, agent)
        return t

    async def reopen(self, task_id: int, agent: str, note: str) -> Task:
        t = self.get(task_id)
        t.status = "todo"
        if note:
            t.notes.append(f"reopened by {agent}: {note}")
        await self._save(t, agent, "reopened")
        return t

    def summary(self, limit: int = 30) -> str:
        if not self.tasks:
            return "(no tasks yet)"
        lines = [t.line() for t in list(self.tasks.values())[-limit:]]
        return "\n".join(lines)

    def load(self, rows: list[dict[str, Any]]) -> None:
        self.tasks = {r["id"]: Task(**r) for r in rows}
