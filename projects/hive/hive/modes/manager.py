"""Manager/workers: a coordinator breaks the goal into tasks and delegates them in parallel.

- The coordinator (the agent with that role, or else the first agent) gets
  the goal and creates tasks, assigning them by name or leaving them open.
- Tasks assigned to a worker go to it as soon as their dependencies are done.
  Open tasks are handed to an idle worker, preferring one whose role matches.
  Workers run in parallel; a worker's final reply completes its task.
- When every task is done (or one fails), the coordinator is told, and can add
  follow-ups or call ``finish_run``. If it replies without doing either while
  everything is done, that reply is taken as the summary.
- Small-model safety nets: a coordinator that creates no tasks is asked once
  more, then the goal becomes a single task for the first worker. Review
  cycles are capped at ``limits.max_rounds``.
"""

from __future__ import annotations

from typing import Any

from ..agents.agent import Agent, InboxItem
from ..context import clip
from .base import Mode

WORKER_TOOLS = ["list_tasks", "fail_task", "handoff_task", "send_message"]
COORDINATOR_TOOLS = ["create_task", "assign_task", "list_tasks", "finish_run"]


class Manager(Mode):
    name = "manager"
    description = "A coordinator splits the goal into tasks; workers do them in parallel."
    default_team = ["coordinator", "coder", "writer"]

    def __init__(self, run: Any):
        super().__init__(run)
        self.asked_again = False
        self.review_cycles = 0
        self.notified_all_done = False

    def coordinator(self) -> Agent:
        c = self.by_role("coordinator")
        return c[0] if c else self.agents[0]

    def workers(self) -> list[Agent]:
        c = self.coordinator()
        return [a for a in self.agents if a is not c]

    def guidance(self, agent: Agent) -> str:
        if agent is self.coordinator():
            names = ", ".join(f"{a.name} ({a.spec.role})" for a in self.workers())
            return (
                f"You coordinate: {names}. Create tasks with create_task and assign_to a teammate's name. "
                "Tasks with no dependencies run in parallel. You'll hear back when all tasks are done."
            )
        return "Do the task you're given. When it's done, reply with a short summary of the result; that completes it. If you can't do it, call fail_task."

    def extra_tools(self, agent: Agent) -> list[str]:
        return COORDINATOR_TOOLS if agent is self.coordinator() else WORKER_TOOLS

    async def start(self) -> None:
        if not self.workers():
            await self.run.finish("failed", reason="manager mode needs a coordinator and at least one worker")
            return
        team = "\n".join(f"- {a.name} ({a.spec.role}): {a.spec.description}" for a in self.workers())
        await self.run.instruct(
            self.coordinator().name,
            f"Goal: {self.run.goal}\n\nYour team:\n{team}\n\n"
            "Break the goal into tasks with create_task. Give each one to the right teammate with assign_to. "
            "Then reply with a one-line summary of the plan.",
            sender="manager",
        )

    # Work distribution ----------------------------------------------------------------------------

    async def _dispatch(self) -> None:
        """Hand open, ready tasks to idle workers."""
        run = self.run
        idle = [w for w in self.workers() if not w.busy and not w.paused and not self._has_open_task(w)]
        for t in run.tasks.ready():
            if t.owner or not idle:
                continue
            match = next((w for w in idle if t.role and w.spec.role == t.role), idle[0])
            idle.remove(match)
            await run.tasks.assign(t.id, match.name, by="manager")

    def _has_open_task(self, agent: Agent) -> bool:
        return any(t.owner == agent.name and t.status in ("todo", "in_progress") for t in self.run.tasks.tasks.values())

    async def on_task(self, task: dict[str, Any], change: str, actor: str | None) -> None:
        if change == "created":
            self.notified_all_done = False
        if task["status"] == "failed":
            await self._tell_coordinator(
                f"Task #{task['id']} ({task['title']}) failed: {'; '.join(task['notes'])}\n"
                "Create a different task for it, reassign it, or call finish_run if the goal is met anyway.\n\n"
                f"Board:\n{self.run.tasks.summary()}"
            )
            return
        await self._dispatch()
        if task["status"] == "done" and self.run.tasks.all_done():
            await self._all_done()

    async def _all_done(self) -> None:
        if self.notified_all_done or self.coordinator().busy:
            return  # mid-turn it may still be adding tasks; its final reply re-checks
        self.notified_all_done = True
        self.review_cycles += 1
        if self.review_cycles > self.run.limits.max_rounds:
            await self.run.finish("needs_attention", summary=self._results(), reason=f"the coordinator kept adding work after {self.run.limits.max_rounds} review cycles")
            return
        await self.run.checkpoint(f"all tasks done (cycle {self.review_cycles})")
        await self._tell_coordinator(
            "All tasks are done. Results:\n" + self._results() + "\n\n"
            "Check the work if you need to (read_file). If something is missing, create follow-up tasks. "
            "Otherwise call finish_run(summary)."
        )

    def _results(self) -> str:
        return "\n".join(f"#{t.id} {t.title} [{t.status}] by {t.owner}: {clip(t.result, 300)}" for t in self.run.tasks.tasks.values())

    async def _tell_coordinator(self, text: str) -> None:
        await self.run.instruct(self.coordinator().name, text, sender="manager")

    # Replies ---------------------------------------------------------------------------------------

    async def on_final(self, agent: Agent, items: list[InboxItem], text: str) -> None:
        await self.route_final(agent, items, text)
        if agent is not self.coordinator() or not any(i.kind == "instruction" for i in items):
            await self._dispatch()
            return
        tasks = self.run.tasks.tasks
        if not tasks:
            if not self.asked_again:
                self.asked_again = True
                await self._tell_coordinator("You haven't created any tasks yet. Use create_task (with assign_to) for each piece of work.")
            else:
                worker = self.workers()[0]
                await self.run.emit("mode.fallback", agent.name, detail=f"no tasks created; giving the whole goal to {worker.name}")
                await self.run.tasks.create(clip(self.run.goal, 120), self.run.goal, created_by="manager", assign_to=worker.name)
            return
        if self.run.tasks.all_done() and self.notified_all_done:
            # Told everything was done, and replied without new tasks or finish_run: take it as the summary.
            await self.deliver(text, by=agent.name)
            return
        await self._dispatch()
        if self.run.tasks.all_done():
            await self._all_done()

    async def on_idle(self) -> None:
        await self._dispatch()
        if self.run.tasks.all_done():
            self.notified_all_done = False
            await self._all_done()
        elif not any(a.busy for a in self.agents) and self.run.tasks.open():
            stuck = [t for t in self.run.tasks.open() if not self.run.tasks.deps_done(t)]
            if stuck:
                await self._tell_coordinator(
                    "Nobody can start the remaining tasks; they wait on tasks that won't finish:\n"
                    + self.run.tasks.summary() + "\nFix the plan (new tasks or reassignments) or call finish_run."
                )

    async def on_restore(self) -> None:
        if not self.run.tasks.tasks:
            await self.start()
        else:
            await self._dispatch()

    def state(self) -> dict[str, Any]:
        return {"asked_again": self.asked_again, "review_cycles": self.review_cycles, "notified_all_done": self.notified_all_done}

    def load_state(self, state: dict[str, Any]) -> None:
        self.asked_again = state.get("asked_again", False)
        self.review_cycles = state.get("review_cycles", 0)
        self.notified_all_done = state.get("notified_all_done", False)
