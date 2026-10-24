"""The Mode interface: a conductor that turns run events into work for agents (DESIGN.md §8).

A mode reacts, one event at a time, to: the run starting, an agent's final
reply, task changes, review verdicts, finish requests, agent errors, and
"everyone is idle". It drives agents through the same task board and
inboxes they use with each other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..agents.agent import Agent, InboxItem
from ..runtime.board import BoardError

if TYPE_CHECKING:
    from ..runtime.run import Run


class Mode:
    name = "base"
    description = ""
    #: Roles used when the user doesn't pick a team.
    default_team: list[str] = ["coder"]
    #: True: an idle team is waiting for the user, not stuck, so the run stays open.
    waits_for_user = False

    def __init__(self, run: "Run"):
        self.run = run

    # Hooks a mode overrides -----------------------------------------------------------------

    def guidance(self, agent: Agent) -> str:
        """Extra system-prompt text for ``agent`` in this mode."""
        return ""

    def extra_tools(self, agent: Agent) -> list[str]:
        """Tools this mode adds to ``agent``'s own list."""
        return []

    async def start(self) -> None:
        """Seed the run: create the first tasks, send the first instructions."""

    async def on_final(self, agent: Agent, items: list[InboxItem], text: str) -> None:
        await self.route_final(agent, items, text)

    async def on_task(self, task: dict[str, Any], change: str, actor: str | None) -> None:
        """A task changed (created, assigned, done, failed, ...)."""

    async def on_verdict(self, agent: str, approved: bool, notes: str) -> None:
        """A reviewer or tester called approve or request_changes."""

    async def on_finish_request(self, agent: str, summary: str) -> None:
        await self.deliver(summary, by=agent)

    async def on_error(self, agent: Agent, items: list[InboxItem], error: str) -> None:
        """An agent's turn failed (usually a model call that failed for good).
        Keep its work for later, pause it, and tell the user."""
        agent._pending[:0] = items
        await agent.pause()
        await self.run.attention(
            f"{agent.name} stopped: {error}. Fix the cause (for example its backend or model), then resume it."
        )

    async def on_idle(self) -> None:
        """Every agent is idle. Hand out more work, or finish. Doing neither ends the run as needs_attention."""

    async def on_restore(self) -> None:
        """After a rollback: re-issue whatever the mode was waiting for. Tasks that were
        in progress have already been handed back to their owners."""

    def state(self) -> dict[str, Any]:
        return {}

    def load_state(self, state: dict[str, Any]) -> None:
        pass

    # Helpers ---------------------------------------------------------------------------------------

    @property
    def agents(self) -> list[Agent]:
        return list(self.run.agents.values())

    def lead(self) -> Agent:
        return self.agents[0]

    def by_role(self, role: str) -> list[Agent]:
        return [a for a in self.agents if a.spec.role == role]

    async def route_final(self, agent: Agent, items: list[InboxItem], text: str) -> None:
        """Default handling of a final reply: answer whoever asked, finish the task worked on."""
        run = self.run
        for it in items:
            if it.kind == "user":
                await run.send(agent.name, "user", text)
            elif it.kind == "message" and it.sender in run.agents:
                await run.send(agent.name, it.sender, text, depth=it.depth + 1)
            elif it.task_id is not None and it.task_id in run.tasks.tasks:
                t = run.tasks.tasks[it.task_id]
                if t.status == "in_progress" and t.owner == agent.name:
                    try:
                        await run.tasks.complete(t.id, agent.name, text)
                    except BoardError:
                        pass

    async def deliver(self, summary: str, by: str) -> None:
        """Write the deliverable to the board and finish the run as done."""
        files = sorted(self.run.files.changed)
        content = summary.strip()
        if files:
            content += "\n\nFiles: " + ", ".join(files)
        await self.run.board.add("deliverable", "Deliverable", content, by)
        await self.run.finish("done", summary=content)
