"""Approval of risky tool calls (shell commands, deletes) by the user (DESIGN.md §10)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ..agents.agent import Agent
    from .run import Run

#: Run-wide override: ask per the agent's policy, approve everything, or refuse everything.
Policy = Literal["ask", "all", "none"]


@dataclass
class Decision:
    approved: bool
    by: str
    reason: str = ""


@dataclass
class Pending:
    id: int
    agent: str
    tool: str
    args: dict[str, Any]
    future: asyncio.Future[Decision]


class Approvals:
    def __init__(self, run: "Run", timeout: float, policy: Policy = "ask"):
        self.run = run
        self.timeout = timeout
        self.policy: Policy = policy
        self.pending: dict[int, Pending] = {}

    async def request(self, agent: "Agent", tool: str, args: dict[str, Any]) -> Decision:
        store = self.run.app.store
        if self.policy == "none" or agent.spec.approval == "deny":
            return await self._auto(agent, tool, args, Decision(False, "policy", "risky actions are turned off for this run"))
        if self.policy == "all" or agent.spec.approval == "allow":
            return await self._auto(agent, tool, args, Decision(True, "policy"))
        id = await store.add_approval(self.run.id, agent.name, tool, args)
        fut: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
        self.pending[id] = Pending(id, agent.name, tool, args, fut)
        await agent.set_status("waiting_approval", tool)
        await self.run.emit("approval.requested", agent.name, id=id, tool=tool, args=args)
        try:
            decision = await asyncio.wait_for(asyncio.shield(fut), self.timeout or None)
        except asyncio.TimeoutError:
            decision = Decision(False, "timeout", f"no answer within {self.timeout:g}s")
            await store.decide_approval(id, "rejected", "timeout", decision.reason)
            await self.run.emit("approval.decided", agent.name, id=id, approved=False, by="timeout", reason=decision.reason)
        finally:
            self.pending.pop(id, None)
        await agent.set_status("thinking")
        return decision

    async def _auto(self, agent: "Agent", tool: str, args: dict[str, Any], d: Decision) -> Decision:
        await self.run.emit("approval.decided", agent.name, id=None, tool=tool, args=args, approved=d.approved, by=d.by, reason=d.reason)
        return d

    async def decide(self, id: int, approved: bool, by: str = "user", reason: str = "") -> bool:
        """Answer a pending request. Returns False if it's no longer pending."""
        p = self.pending.get(id)
        if p is None or p.future.done():
            return False
        await self.run.app.store.decide_approval(id, "approved" if approved else "rejected", by, reason or None)
        await self.run.emit("approval.decided", p.agent, id=id, tool=p.tool, approved=approved, by=by, reason=reason)
        p.future.set_result(Decision(approved, by, reason))
        return True

    async def reject_all(self, reason: str) -> None:
        for id in list(self.pending):
            await self.decide(id, False, "system", reason)
