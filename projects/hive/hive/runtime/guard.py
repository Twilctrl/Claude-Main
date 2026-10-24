"""Loop protection (DESIGN.md §9): stop small models spinning forever.

Soft rules (repeats, identical tool calls, stalls, ping-pong, protocol
failures) first nudge the agent with a note in its history; the second time
they pause the agent and ask the user. Hard budgets (tokens, wall-clock, run
turns) stop the run as ``budget_exhausted``.
"""

from __future__ import annotations

import difflib
import json
import re
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from ..config import Limits

if TYPE_CHECKING:
    from ..agents.agent import Agent
    from .run import Run

NUDGES = {
    "repeat": "You are repeating yourself. Try a different approach, or finish with a short summary of where things stand.",
    "same_tool_call": "You made the same tool call several times in a row and got the same result. Do something different.",
    "stall": "The team hasn't made progress for a while. Do one concrete thing now (write a file, finish or fail a task), or finish.",
    "pingpong": "You and {other} keep messaging without making progress. Stop messaging and do the work, or finish.",
    "protocol": 'Your replies keep breaking the format. Reply with ONLY one JSON object, e.g. {"final": "..."}.',
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()[:2000]


def similar(a: str, b: str) -> float:
    if a == b:
        return 1.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < 0.5 or sm.quick_ratio() < 0.5:
        return 0.0
    return sm.ratio()


class LoopGuard:
    def __init__(self, run: "Run", limits: Limits):
        self.run = run
        self.limits = limits
        self.started = time.monotonic()
        self.replies: dict[str, deque[str]] = {}
        self.calls: dict[str, deque[str]] = {}
        self.turns_since_progress = 0
        self.pairs: dict[frozenset[str], int] = {}
        self.strikes: dict[tuple[str, str], int] = {}
        self.extra_seconds = 0.0  # added when the user raises the budget mid-run

    # Hard budgets ---------------------------------------------------------------------

    def check_budget(self) -> str | None:
        lim, r = self.limits, self.run
        if r.tokens_used >= lim.max_tokens_per_run:
            return f"token budget used up ({r.tokens_used} of {lim.max_tokens_per_run})"
        if r.turns_used >= lim.max_turns_per_run:
            return f"turn budget used up ({r.turns_used} of {lim.max_turns_per_run} turns)"
        if time.monotonic() - self.started >= lim.max_wall_seconds + self.extra_seconds:
            return f"time budget used up ({lim.max_wall_seconds}s)"
        return None

    def before_turn(self, agent: "Agent") -> str | None:
        if agent.turns >= self.limits.max_turns_per_agent:
            return f"{agent.name} reached its turn limit ({self.limits.max_turns_per_agent})"
        return None

    # Soft rules -------------------------------------------------------------------------

    def after_reply(self, agent: "Agent", text: str) -> None:
        self.turns_since_progress += 1
        n = max(2, self.limits.repeat_threshold)
        dq = self.replies.setdefault(agent.name, deque(maxlen=n))
        dq.append(_norm(text))
        if len(dq) == n and all(similar(dq[i], dq[i + 1]) >= self.limits.repeat_similarity for i in range(n - 1)):
            self._strike(agent, "repeat", f"{n} near-identical replies in a row")
            dq.clear()
        if self.turns_since_progress >= self.limits.stall_turns:
            self.turns_since_progress = 0
            self._strike(agent, "stall", f"{self.limits.stall_turns} turns without progress")

    def on_tool_call(self, agent: "Agent", name: str, args: dict[str, Any]) -> None:
        n = max(2, self.limits.repeat_threshold)
        dq = self.calls.setdefault(agent.name, deque(maxlen=n))
        dq.append(name + json.dumps(args, sort_keys=True, default=str))
        if len(dq) == n and len(set(dq)) == 1:
            self._strike(agent, "same_tool_call", f"{n} identical {name} calls in a row")
            dq.clear()

    def on_progress(self, agent_name: str | None = None) -> None:
        self.turns_since_progress = 0
        self.pairs.clear()
        if agent_name:
            self.calls.pop(agent_name, None)

    def on_message(self, sender: str, recipient: str) -> None:
        if sender not in self.run.agents or recipient not in self.run.agents:
            return
        key = frozenset((sender, recipient))
        self.pairs[key] = self.pairs.get(key, 0) + 1
        if self.pairs[key] >= self.limits.pingpong_messages:
            self.pairs[key] = 0
            agent = self.run.agents[sender]
            self._strike(agent, "pingpong", f"{self.limits.pingpong_messages} messages with {recipient} without progress", other=recipient)

    def on_protocol_failure(self, agent: "Agent") -> None:
        if agent.protocol_failures >= self.limits.protocol_failures:
            agent.protocol_failures = 0
            self._strike(agent, "protocol", f"{self.limits.protocol_failures} malformed replies in a row")

    def _strike(self, agent: "Agent", rule: str, detail: str, **fmt: str) -> None:
        key = (agent.name, rule)
        self.strikes[key] = self.strikes.get(key, 0) + 1
        from ..backends import ChatMessage

        if self.strikes[key] == 1:
            agent.history.append(ChatMessage("user", "[Hive] " + NUDGES[rule].format(**fmt)))
            self.run.spawn(self.run.emit("guard.trigger", agent.name, rule=rule, action="nudge", detail=detail))
        else:
            self.strikes[key] = 0
            self.run.spawn(self._pause(agent, rule, detail))

    async def _pause(self, agent: "Agent", rule: str, detail: str) -> None:
        await agent.pause()
        await self.run.emit("guard.trigger", agent.name, rule=rule, action="paused", detail=detail)
        await self.run.attention(f"{agent.name} was paused by the loop guard: {detail}. Resume it, give it a hint, or stop the run.")
