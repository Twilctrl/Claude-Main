"""A run: one team working on one goal in one workspace (DESIGN.md §2, §8).

The Run owns the agents and the shared state (task board, blackboard, file
tracker, approvals, loop guard), writes every change to the event log, and
feeds the mode (the conductor) a queue of what happened: agents' final
replies, task changes, verdicts, and "everyone is idle".
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from ..agents.agent import Agent, AgentSpec, AgentStopped, InboxItem
from ..config import Limits
from ..context import clip
from ..tools import Tool, all_tools
from .approvals import Approvals, Policy
from .board import Blackboard, BoardError, Task, TaskBoard
from .events import Event
from .files import FileTracker
from .guard import LoopGuard
from .workspace import Workspace

if TYPE_CHECKING:
    from ..modes.base import Mode
    from .app import Hive

log = logging.getLogger("hive.run")

FINAL_STATUSES = ("done", "failed", "stopped", "needs_attention", "budget_exhausted", "interrupted")


class Run:
    def __init__(
        self,
        app: Hive,
        run_id: str,
        project: dict[str, Any],
        goal: str,
        mode: str,
        team: list[AgentSpec],
        limits: Limits,
        approval: Policy = "ask",
    ):
        from ..modes import create_mode

        self.app = app
        self.id = run_id
        self.project = project
        self.goal = goal
        self.mode_name = mode
        self.limits = limits
        self.workspace = Workspace(project["workspace"])
        self.status = "created"
        self.attention_msg = ""
        self.summary = ""
        self.seq = 0
        self.tokens_used = 0
        self.turns_used = 0
        self.started_at = time.time()
        self._emit_lock = asyncio.Lock()
        self._bg: set[asyncio.Task[Any]] = set()
        self._mode_q: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._mode_busy = False
        self._idle_reported = False  # report "idle" once per stretch of inactivity
        self._mode_task: asyncio.Task[None] | None = None
        self.done = asyncio.Event()

        store = app.store
        self.tasks = TaskBoard(store, run_id, self.emit)
        self.tasks.on_assigned = self._deliver_task
        self.board = Blackboard(store, run_id, self.emit)
        self.files = FileTracker(store, project["id"], run_id, self.workspace, self.emit)
        self.approvals = Approvals(self, app.settings.hive.approval_timeout, approval)
        self.guard = LoopGuard(self, limits)
        self.agents: dict[str, Agent] = {}
        for spec in team:
            if spec.name in self.agents or spec.name in ("user", "#channel", "system"):
                raise ValueError(f"agent name {spec.name!r} is taken or reserved")
            self.agents[spec.name] = Agent(spec, self)
        self.mode: Mode = create_mode(mode, self)

    # Events -------------------------------------------------------------------------------

    async def emit(self, kind: str, agent: str | None = None, /, **payload: Any) -> Event:
        """Log an event, then publish it."""
        async with self._emit_lock:
            self.seq += 1
            ev = Event(self.id, self.seq, time.time(), kind, agent, payload)
            await self.app.store.append_event(self.id, ev.seq, ev.ts, kind, agent, payload)
            self.app.bus.publish(ev)
        if kind == "task.updated":
            self._mode_q.put_nowait(("task", {"task": payload["task"], "change": payload["change"], "actor": agent}))
        return ev

    def live(self, kind: str, agent: str | None = None, /, **payload: Any) -> None:
        """Publish without storing (token deltas)."""
        self.app.bus.publish(Event(self.id, 0, time.time(), kind, agent, payload))

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Run ``coro`` in the background, keeping a reference and logging failures."""
        t = asyncio.create_task(coro)
        self._bg.add(t)

        def done(task: asyncio.Task[Any]) -> None:
            self._bg.discard(task)
            if not task.cancelled() and task.exception():
                log.error("background task failed", exc_info=task.exception())

        t.add_done_callback(done)
        return t

    # Lifecycle --------------------------------------------------------------------------------

    def config(self) -> dict[str, Any]:
        return {
            "team": [a.spec.model_dump() for a in self.agents.values()],
            "limits": self.limits.model_dump(),
            "approval": self.approvals.policy,
        }

    async def start(self) -> None:
        store = self.app.store
        await store.add_run(self.id, self.project["id"], self.mode_name, self.goal, self.config())
        for a in self.agents.values():
            await store.upsert_agent(self.id, a.name, a.spec.role, a.spec.model_dump(), a.status)
        await self.files.load()
        await self.set_status("running")
        await self.emit(
            "run.started",
            goal=self.goal,
            mode=self.mode_name,
            project=self.project["name"],
            workspace=str(self.workspace.root),
            team=[{"name": a.name, "role": a.spec.role, "backend": a.spec.backend, "model": a.spec.model} for a in self.agents.values()],
            limits=self.limits.model_dump(),
        )
        await self.checkpoint("start")
        for a in self.agents.values():
            a.start()
        self._mode_task = asyncio.create_task(self._mode_loop(), name=f"mode-{self.id}")
        self._mode_q.put_nowait(("start", {}))

    async def set_status(self, status: str, reason: str = "") -> None:
        self.status = status
        await self.app.store.update_run(self.id, status=status)
        await self.emit("run.status", status=status, reason=reason)

    async def wait(self, timeout: float | None = None) -> str:
        await asyncio.wait_for(self.done.wait(), timeout)
        return self.status

    async def finish(self, status: str, summary: str = "", reason: str = "") -> None:
        """End the run. Idempotent."""
        if self.done.is_set() or self.status in FINAL_STATUSES:
            return
        self.status = status
        if summary:
            self.summary = summary
        await self.approvals.reject_all("the run ended")
        for a in self.agents.values():
            await a.stop()
        if self._mode_task and asyncio.current_task() is not self._mode_task:
            self._mode_task.cancel()
        for a in self.agents.values():
            await self.save_agent(a)
        stats = self.stats()
        await self.app.store.update_run(self.id, status=status, ended_at=time.time(), summary=self.summary, stats=stats)
        await self.emit("run.finished", status=status, reason=reason, summary=self.summary, stats=stats)
        self.done.set()
        for t in list(self._bg):
            if t is not asyncio.current_task():
                t.cancel()

    async def stop(self, reason: str = "stopped by the user") -> None:
        await self.finish("stopped", reason=reason)

    def stats(self) -> dict[str, Any]:
        tasks = list(self.tasks.tasks.values())
        return {
            "tokens": self.tokens_used,
            "turns": self.turns_used,
            "seconds": round(time.time() - self.started_at, 1),
            "tasks": {s: sum(t.status == s for t in tasks) for s in ("todo", "in_progress", "done", "failed")},
            "agents": {a.name: {"turns": a.turns, "tokens": a.tokens} for a in self.agents.values()},
        }

    async def save_agent(self, a: Agent) -> None:
        await self.app.store.upsert_agent(self.id, a.name, a.spec.role, a.spec.model_dump(), a.status, a.state())

    # Steering ----------------------------------------------------------------------------------

    async def pause(self, agent: str | None = None) -> None:
        targets = [self.agent(agent)] if agent else list(self.agents.values())
        for a in targets:
            await a.pause()
        if not agent:
            await self.set_status("paused")

    async def resume(self, agent: str | None = None) -> None:
        targets = [self.agent(agent)] if agent else list(self.agents.values())
        self.guard.on_progress()
        for a in targets:
            await a.resume()
        self.activity()
        if all(not a.paused for a in self.agents.values()):
            self.attention_msg = ""
            if self.status == "paused":
                await self.set_status("running")
        self.spawn(self._check_idle())

    async def attention(self, message: str) -> None:
        """Something needs the user (the guard paused an agent, a backend stayed down)."""
        self.attention_msg = message
        await self.emit("run.attention", message=message)

    async def update_agent(self, name: str, **changes: Any) -> AgentSpec:
        """Edit an agent mid-run (prompt, params, tools, model). Applies from its next model call."""
        a = self.agent(name)
        allowed = {"system_prompt", "params", "tools", "model", "backend", "context_budget", "approval", "wake_on_broadcast", "fallbacks"}
        bad = set(changes) - allowed
        if bad:
            raise ValueError(f"can't change {', '.join(sorted(bad))}")
        if "backend" in changes:
            self.app.scheduler.backend(changes["backend"])
        new = AgentSpec.model_validate({**a.spec.model_dump(), **changes})
        a.spec = new
        await self.save_agent(a)
        await self.emit("agent.updated", name, changes={k: clip(str(v), 2000) for k, v in changes.items()})
        return new

    def agent(self, name: str) -> Agent:
        try:
            return self.agents[name]
        except KeyError:
            raise BoardError(f"no agent named {name!r} (team: {', '.join(self.agents)})") from None

    # Messaging --------------------------------------------------------------------------------------

    async def send(self, sender: str, recipient: str, content: str, depth: int = 0) -> None:
        """Deliver a message. Replies between agents stop waking the recipient at depth 2,
        so two agents can't thank each other forever."""
        if recipient not in self.agents and recipient not in ("#channel", "user"):
            raise BoardError(f"no teammate named {recipient!r} (team: {', '.join(self.agents)}, or 'user')")
        if recipient == sender:
            raise BoardError("you can't message yourself")
        ev = await self.emit("message", sender, sender=sender, recipient=recipient, content=content, depth=depth)
        await self.app.store.add_message(self.id, ev.seq, sender, recipient, content, depth)
        if recipient == "#channel":
            for a in self.agents.values():
                if a.name == sender:
                    continue
                if sender == "user":
                    a.deliver(InboxItem("user", "user", content))
                elif a.spec.wake_on_broadcast:
                    a.deliver(InboxItem("message", sender, content, depth=depth))
                else:
                    a.add_digest(f"{sender}: {clip(content, 400)}")
        elif recipient in self.agents:
            a = self.agents[recipient]
            if sender == "user":
                a.deliver(InboxItem("user", "user", content))
            elif depth < 2:
                self.guard.on_message(sender, recipient)
                a.deliver(InboxItem("message", sender, content, depth=depth))
            else:
                a.add_digest(f"{sender} (to you): {clip(content, 400)}")

    async def user_message(self, to: str, text: str) -> None:
        """The user speaks to one agent, or to everyone with '#channel'."""
        self.guard.on_progress()
        await self.send("user", to, text)

    async def _deliver_task(self, task: Task, by: str) -> None:
        if task.owner not in self.agents:
            return
        body = task.title + (f"\n{task.description}" if task.description else "")
        if task.notes:
            body += "\nNotes: " + " | ".join(task.notes[-3:])
        self.agents[task.owner].deliver(InboxItem("task", by, body, task_id=task.id))

    async def instruct(self, agent: str, text: str, task_id: int | None = None, sender: str | None = None) -> None:
        """A message from the mode (the conductor) to one agent."""
        self.agent(agent).deliver(InboxItem("instruction", sender or self.mode_name, text, task_id=task_id))

    # Tools ----------------------------------------------------------------------------------------------

    def tools_for(self, agent: Agent) -> list[Tool]:
        registry = all_tools()
        names = list(dict.fromkeys([*agent.spec.tools, *self.mode.extra_tools(agent)]))
        return [registry[n] for n in names if n in registry]

    # Mode plumbing ---------------------------------------------------------------------------------------

    async def mode_event(self, kind: str, **data: Any) -> None:
        self._mode_q.put_nowait((kind, data))

    async def on_final(self, agent: Agent, items: list[InboxItem], text: str) -> None:
        self._mode_q.put_nowait(("final", {"agent": agent, "items": items, "text": text}))
        self.spawn(self.save_agent(agent))

    async def on_agent_error(self, agent: Agent, items: list[InboxItem], exc: BaseException) -> None:
        if isinstance(exc, AgentStopped) and str(exc):
            # Not from inside the agent's own task: finish() stops (and awaits) every agent.
            self.spawn(self.finish("budget_exhausted", reason=str(exc)))
            return
        self._mode_q.put_nowait(("error", {"agent": agent, "items": items, "error": f"{type(exc).__name__}: {exc}"}))

    def agent_idle(self, agent: Agent) -> None:
        self.spawn(self._check_idle())

    def all_idle(self) -> bool:
        return (
            not any(a.busy for a in self.agents.values())
            and self._mode_q.empty()
            and not self._mode_busy
            and not self.approvals.pending
        )

    async def _check_idle(self) -> None:
        await asyncio.sleep(0)
        if self.done.is_set() or self._idle_reported or not self.all_idle():
            return
        self._idle_reported = True
        self._mode_q.put_nowait(("idle", {}))

    def activity(self) -> None:
        """Something happened (a turn started, the mode got an event): idle may be reported again."""
        self._idle_reported = False

    async def _mode_loop(self) -> None:
        m = self.mode
        while not self.done.is_set():
            kind, data = await self._mode_q.get()
            self._mode_busy = True
            if kind != "idle":
                self.activity()
            try:
                if kind == "start":
                    await m.start()
                elif kind == "restore":
                    await m.on_restore()
                elif kind == "final":
                    await m.on_final(data["agent"], data["items"], data["text"])
                elif kind == "task":
                    await m.on_task(data["task"], data["change"], data["actor"])
                elif kind == "verdict":
                    await m.on_verdict(data["agent"], data["approved"], data["notes"])
                elif kind == "finish":
                    await m.on_finish_request(data["agent"], data["summary"])
                elif kind == "error":
                    await m.on_error(data["agent"], data["items"], data["error"])
                elif kind == "idle":
                    if self.all_idle_except_mode():
                        await m.on_idle()
                        await asyncio.sleep(0)
                        if not self.done.is_set() and self.all_idle_except_mode() and self._mode_q.empty():
                            await self._stuck()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("mode %s failed on %s", self.mode_name, kind)
                await self.emit("mode.error", error=f"{type(e).__name__}: {e}", on=kind)
                await self.finish("failed", reason=f"the {self.mode_name} mode crashed: {e}")
            finally:
                self._mode_busy = False
            if not self.done.is_set() and self._mode_q.empty():
                self.spawn(self._check_idle())

    def all_idle_except_mode(self) -> bool:
        return not any(a.busy for a in self.agents.values()) and not self.approvals.pending

    async def _stuck(self) -> None:
        """Everyone is idle and the mode didn't finish or hand out more work."""
        if any(a.paused for a in self.agents.values()) or self.status == "paused":
            return  # waiting on the user, not stuck
        if self.mode.waits_for_user:
            await self.emit("run.idle")
            return
        await self.finish(
            "needs_attention",
            reason="every agent is idle but the goal isn't marked finished. Check the board, then continue the run or start a new one.",
        )

    # Checkpoints ---------------------------------------------------------------------------------------------

    async def rollback(self, checkpoint_id: int) -> None:
        """Put files, tasks, board, agent histories and the mode back as they were at a checkpoint,
        then carry on from there. Later events stay in the log, marked rolled back."""
        cp = await self.app.store.get_checkpoint(checkpoint_id)
        if not cp or cp["run_id"] != self.id:
            raise BoardError(f"no checkpoint {checkpoint_id} in this run")
        if self.done.is_set():
            raise BoardError("the run has ended; start a new run in the same project instead")
        for a in self.agents.values():
            await a.pause()
            a.interrupt()
        for _ in range(100):  # let interrupted turns unwind
            if not any(a.in_turn for a in self.agents.values()):
                break
            await asyncio.sleep(0.02)
        await self.approvals.reject_all("rolled back")
        while not self._mode_q.empty():
            self._mode_q.get_nowait()
        await self._restore(cp, "checkpoint.restored")
        await self.resume()

    async def reopen(self, cp: dict[str, Any]) -> None:
        """Continue a run that ended (crash, shutdown, stop, budget) from checkpoint ``cp``,
        in this fresh Run object. Work after the checkpoint is rolled back."""
        store = self.app.store
        self.seq = await store.max_seq(self.id)
        state = cp["state"]
        self.tokens_used = state.get("tokens_used", 0)
        self.turns_used = state.get("turns_used", 0)
        await self.files.load()
        await store.update_run(self.id, ended_at=None)
        await self.set_status("running", "resumed")
        for a in self.agents.values():
            a.start()
        self._mode_task = asyncio.create_task(self._mode_loop(), name=f"mode-{self.id}")
        await self._restore(cp, "run.resumed")

    async def _restore(self, cp: dict[str, Any], event: str) -> None:
        state = cp["state"]
        await self.files.restore(state["files"])
        self.tasks.load(state["tasks"])
        await self.app.store.replace_tasks(self.id, state["tasks"])
        self.board.load(state["board"])
        await self.app.store.replace_board(self.id, state["board"])
        for name, s in state["agents"].items():
            if name in self.agents:
                self.agents[name].load_state(s)
        self.mode.load_state(state.get("mode", {}))
        await self.app.store.mark_rolled_back(self.id, cp["seq"], self.seq)
        await self.emit(event, id=cp["id"], label=cp["label"], seq=cp["seq"], tasks=state["tasks"], board=state["board"])
        self.guard.on_progress()
        # Work that was in flight at the checkpoint is handed out again.
        for t in self.tasks.tasks.values():
            if t.status == "in_progress":
                t.status = "todo"
                await self.app.store.upsert_task(self.id, t.id, t.to_dict())
                if t.owner and t.owner in self.agents and not any(i.task_id == t.id for i in self.agents[t.owner]._pending):
                    await self._deliver_task(t, "rollback")
        # Through the mode's queue, like "start", so idle checks wait until the mode has re-issued its work.
        self._mode_q.put_nowait(("restore", {}))

    async def checkpoint(self, label: str) -> int:
        state = {
            "tasks": [t.to_dict() for t in self.tasks.tasks.values()],
            "board": [e.to_dict() for e in self.board.entries],
            "agents": {a.name: a.state() for a in self.agents.values()},
            "files": await self.files.snapshot(),
            "tokens_used": self.tokens_used,
            "turns_used": self.turns_used,
            "mode": self.mode.state(),
        }
        cid = await self.app.store.add_checkpoint(self.id, self.seq, label, state)
        await self.emit("checkpoint.created", id=cid, label=label, files=len(state["files"]))
        return cid
