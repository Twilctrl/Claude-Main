"""Solo: the first agent takes the whole goal as one task. Others help only when asked."""

from __future__ import annotations

from typing import Any

from .base import Mode


class Solo(Mode):
    name = "solo"
    description = "One agent does the whole goal; teammates answer its messages."
    default_team = ["coder"]

    def __init__(self, run: Any):
        super().__init__(run)
        self.main_task: int | None = None

    def guidance(self, agent: Any) -> str:
        if agent is self.lead():
            return "You own the goal. Do the work, then reply with a summary of what you made."
        return f"Answer {self.lead().name}'s questions when asked."

    async def start(self) -> None:
        lead = self.lead()
        t = await self.run.tasks.create(
            self.run.goal[:120],
            f"{self.run.goal}\n\nWhen it's done, reply with a short summary of what you made and where.",
            created_by="solo",
            assign_to=lead.name,
        )
        self.main_task = t.id

    async def on_task(self, task: dict[str, Any], change: str, actor: str | None) -> None:
        if task["id"] == self.main_task and task["status"] in ("done", "failed"):
            if task["status"] == "done":
                await self.deliver(task["result"], by=task["owner"] or actor or "solo")
            else:
                await self.run.finish("failed", reason="; ".join(task["notes"]) or "the task failed")

    async def on_restore(self) -> None:
        if self.main_task is None:
            await self.start()

    def state(self) -> dict[str, Any]:
        return {"main_task": self.main_task}

    def load_state(self, state: dict[str, Any]) -> None:
        self.main_task = state.get("main_task")
