"""Pipeline: the team works in order, like Planner → Coder → Reviewer → Tester.

Each agent is a stage, classified by role:

- **plan** (planner): turns the goal into tasks on the board.
- **make** (coder, writer, researcher, anyone else): does the work. The first
  make stage gets the plan's tasks one at a time, in dependency order; later
  make stages get one task that carries the previous stage's output.
- **check** (reviewer, tester, judge): gets the diff since the work began
  and must ``approve`` or ``request_changes``. Changes go back to the nearest
  earlier make stage as a new task, up to ``limits.max_rounds`` rounds.

The run is done when the last check stage approves.
"""

from __future__ import annotations

import re
from typing import Any

from ..agents.agent import Agent, InboxItem
from ..context import clip
from ..runtime.files import changes_since
from .base import Mode

PLAN_ROLES = {"planner"}
CHECK_ROLES = {"reviewer", "tester", "judge"}

_CHANGES_HINT = re.compile(r"request_changes|changes requested|\bnot approved?\b|\bmust\b|\bbug\b|\bmissing\b|\bfails?\b|\bbroken\b", re.I)


def stage_kind(agent: Agent) -> str:
    if agent.spec.role in PLAN_ROLES:
        return "plan"
    if agent.spec.role in CHECK_ROLES:
        return "check"
    return "make"


class Pipeline(Mode):
    name = "pipeline"
    description = "Stages in order: plan, make, check. Checks can send work back."
    default_team = ["planner", "coder", "reviewer", "tester"]

    def __init__(self, run: Any):
        super().__init__(run)
        self.stage = -1
        self.round = 1
        self.stage_task: int | None = None  # the task the current plan/make stage is working on
        self.awaiting_verdict: str | None = None
        self.work_start_seq = 0  # diffs for checks start here
        self.last_output = ""
        self.outputs: list[str] = []  # what each finished task reported, for the deliverable
        self.checks: list[str] = []
        self.plan_done = False

    # Prompts -------------------------------------------------------------------------------

    def guidance(self, agent: Agent) -> str:
        kind = stage_kind(agent)
        order = " → ".join(a.name for a in self.agents)
        head = f"The team works as a pipeline: {order}."
        if kind == "plan":
            return head + " Create the tasks with create_task (don't assign them; the pipeline hands them out), then reply with a short summary of the plan."
        if kind == "check":
            return head + " When you're asked to check work, call approve(notes) or request_changes(notes). Always call one of them."
        return head + " Do the task you're given. When it's done, reply with a short summary of what you changed; that completes the task."

    def extra_tools(self, agent: Agent) -> list[str]:
        kind = stage_kind(agent)
        if kind == "check":
            return ["approve", "request_changes"]
        if kind == "plan":
            return ["create_task", "list_tasks"]
        return []

    # Flow -------------------------------------------------------------------------------------

    async def start(self) -> None:
        await self._advance()

    async def _advance(self) -> None:
        """Start the next stage, or finish."""
        self.stage += 1
        if self.stage >= len(self.agents):
            summary = "\n".join(self.outputs) or self.last_output or "The pipeline finished."
            if self.checks:
                summary += "\n\n" + "\n".join(self.checks)
            await self.deliver(summary, by=self.agents[-1].name)
            return
        agent = self.agents[self.stage]
        await self.run.emit("mode.stage", agent.name, stage=self.stage, kind=stage_kind(agent), round=self.round)
        kind = stage_kind(agent)
        if kind == "plan":
            t = await self.run.tasks.create(
                "Plan the work",
                f"Goal: {self.run.goal}\nBreak it into a few concrete tasks with create_task, in order.",
                created_by="pipeline",
                assign_to=agent.name,
            )
            self.stage_task = t.id
        elif kind == "make":
            if self.work_start_seq == 0:
                self.work_start_seq = self.run.seq
            await self._next_make_task(agent)
        else:
            await self._ask_check(agent)

    def _plan_tasks(self) -> list[Any]:
        return [t for t in self.run.tasks.tasks.values() if t.created_by in {a.name for a in self.agents if stage_kind(a) == "plan"}]

    def _first_make(self) -> Agent | None:
        return next((a for a in self.agents if stage_kind(a) == "make"), None)

    async def _next_make_task(self, agent: Agent) -> None:
        run = self.run
        if agent is self._first_make():
            pending = [t for t in self._plan_tasks() if t.status == "todo"]
            ready = [t for t in pending if run.tasks.deps_done(t)]
            if ready:
                self.stage_task = ready[0].id
                await run.tasks.assign(ready[0].id, agent.name, by="pipeline")
                return
            if pending:  # tasks left but none ready (a dependency failed): stop here
                await run.finish("needs_attention", reason="the remaining plan tasks depend on tasks that failed")
                return
            if self._plan_tasks():
                await self._stage_done()
                return
        # No plan, or a later make stage: one task for the whole stage.
        prev = f"\n\nWhat the previous stage reported:\n{clip(self.last_output, 1500)}" if self.last_output else ""
        t = await run.tasks.create(
            f"Do the work: {clip(run.goal, 80)}",
            f"Goal: {run.goal}{prev}",
            created_by="pipeline",
            assign_to=agent.name,
        )
        self.stage_task = t.id

    async def _stage_done(self) -> None:
        self.stage_task = None
        await self.run.checkpoint(f"after {self.agents[self.stage].name}")
        await self._advance()

    async def _ask_check(self, agent: Agent) -> None:
        changed, diff = await changes_since(self.run.app.store, self.run.project["id"], self.run.id, self.work_start_seq)
        files = ", ".join(changed) if changed else "(no files changed)"
        text = (
            f"Review round {self.round} of {self.run.limits.max_rounds}.\n"
            f"Goal: {self.run.goal}\n"
            f"Files changed: {files}\n"
            + (f"\n{diff}\n" if diff else "")
            + (f"\nWhat the previous stage reported:\n{clip(self.last_output, 1200)}\n" if self.last_output else "")
            + "\nCheck the work (read the files if you need more), then call approve(notes) or request_changes(notes)."
        )
        self.awaiting_verdict = agent.name
        await self.run.instruct(agent.name, text)

    # Events -------------------------------------------------------------------------------------

    async def on_task(self, task: dict[str, Any], change: str, actor: str | None) -> None:
        if task["id"] != self.stage_task or task["status"] not in ("done", "failed"):
            return
        agent = self.agents[self.stage]
        if task["status"] == "failed":
            await self.run.finish("needs_attention", reason=f"{agent.name} couldn't finish task #{task['id']}: {'; '.join(task['notes'])}")
            return
        self.last_output = task["result"]
        self.outputs.append(f"#{task['id']} {task['title']} ({agent.name}): {clip(task['result'], 400)}")
        if stage_kind(agent) == "plan":
            self.plan_done = True
            await self._stage_done()
        elif agent is self._first_make() and task["id"] in {t.id for t in self._plan_tasks()}:
            self.stage_task = None
            await self.run.checkpoint(f"task #{task['id']} done")
            await self._next_make_task(agent)  # the next plan task, or the end of the stage
        else:
            await self._stage_done()  # a stage's single task, or a revision

    async def on_final(self, agent: Agent, items: list[InboxItem], text: str) -> None:
        await self.route_final(agent, items, text)
        if self.awaiting_verdict == agent.name and any(i.kind == "instruction" for i in items):
            # A check stage replied without calling approve or request_changes: read its text.
            await self.on_verdict(agent.name, not _CHANGES_HINT.search(text), text)

    async def on_verdict(self, agent: str, approved: bool, notes: str) -> None:
        if self.awaiting_verdict != agent:
            return
        self.awaiting_verdict = None
        await self.run.emit("mode.verdict", agent, approved=approved, notes=clip(notes, 2000), round=self.round)
        if approved:
            self.checks.append(f"{agent} approved (round {self.round}): {clip(notes.strip(), 300) or 'no notes'}")
            await self._stage_done()
            return
        if self.round >= self.run.limits.max_rounds:
            await self.run.finish(
                "needs_attention",
                summary=self.last_output,
                reason=f"{agent} still wants changes after {self.round} rounds: {clip(notes, 500)}",
            )
            return
        self.round += 1
        target = next((i for i in range(self.stage - 1, -1, -1) if stage_kind(self.agents[i]) == "make"), None)
        if target is None:
            await self.run.finish("needs_attention", reason=f"{agent} requested changes but no stage can make them")
            return
        self.stage = target
        maker = self.agents[target]
        await self.run.emit("mode.stage", maker.name, stage=target, kind="make", round=self.round)
        t = await self.run.tasks.create(
            f"Address review feedback (round {self.round})",
            f"{agent} asked for changes:\n{notes}\n\nGoal: {self.run.goal}\nMake the changes, then reply with a summary.",
            created_by=agent,
            assign_to=maker.name,
        )
        self.stage_task = t.id

    async def on_idle(self) -> None:
        # Recover from a check stage that ended its turn without any reply we could read.
        if self.awaiting_verdict:
            await self.run.instruct(self.awaiting_verdict, "Please finish your check: call approve(notes) or request_changes(notes).")

    async def on_restore(self) -> None:
        if self.awaiting_verdict:
            await self._ask_check(self.run.agents[self.awaiting_verdict])
            return
        if self.stage_task is not None:
            return  # the run re-delivers the stage's unfinished task
        agent = self.agents[self.stage] if 0 <= self.stage < len(self.agents) else None
        if agent is not None and agent is self._first_make() and any(t.status == "todo" for t in self._plan_tasks()):
            await self._next_make_task(agent)  # checkpointed between two plan tasks
        else:
            await self._advance()  # checkpointed between stages

    def state(self) -> dict[str, Any]:
        keys = ("stage", "round", "stage_task", "awaiting_verdict", "work_start_seq", "last_output", "outputs", "checks", "plan_done")
        return {k: getattr(self, k) for k in keys}

    def load_state(self, state: dict[str, Any]) -> None:
        for k, v in state.items():
            setattr(self, k, v)
