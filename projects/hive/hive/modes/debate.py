"""Debate: several agents propose, critique each other, revise, and a judge decides.

Proposers are every agent except the judge (the agent with the judge role,
or else the last agent). The phases are propose → critique → revise, with
critique and revise repeated ``limits.debate_rounds`` times (default 1; 0
skips straight to the judge). Every proposer in a phase works in
parallel; the scheduler queues them on slow backends. The judge then picks
or merges, and writes files if the goal asks for them.
"""

from __future__ import annotations

from typing import Any

from ..agents.agent import Agent, InboxItem
from ..context import clip
from .base import Mode


class Debate(Mode):
    name = "debate"
    description = "Proposers answer in parallel, critique each other, revise; a judge picks or merges."
    default_team = ["coder", "coder", "judge"]

    def __init__(self, run: Any):
        super().__init__(run)
        self.phase = ""
        self.round = 0
        self.waiting: set[str] = set()
        self.answers: dict[str, str] = {}  # latest proposal per agent
        self.critiques: dict[str, str] = {}
        self.prompts: dict[str, str] = {}  # the current phase's prompts, to re-send after a rollback

    def judge(self) -> Agent:
        judges = self.by_role("judge")
        return judges[0] if judges else self.agents[-1]

    def proposers(self) -> list[Agent]:
        j = self.judge()
        return [a for a in self.agents if a is not j]

    def guidance(self, agent: Agent) -> str:
        if agent is self.judge():
            return "You are the judge of a debate. When asked, compare the proposals and decide."
        return "You are in a debate with other agents. Answer each step briefly and concretely, in plain text."

    # Phases ----------------------------------------------------------------------------------

    async def start(self) -> None:
        if not self.proposers():
            await self.run.finish("failed", reason="debate needs at least one proposer and a judge")
            return
        await self._phase("propose", {a.name: f"Propose your answer to the goal.\nGoal: {self.run.goal}\nReply with your proposal only." for a in self.proposers()})

    async def _phase(self, phase: str, prompts: dict[str, str]) -> None:
        self.phase = phase
        self.prompts = dict(prompts)
        self.waiting = set(prompts)
        await self.run.emit("mode.phase", phase=phase, round=self.round, agents=sorted(prompts))
        for name, text in prompts.items():
            await self.run.instruct(name, text, sender="debate")

    def _others(self, name: str, source: dict[str, str]) -> str:
        return "\n\n".join(f"### {n}\n{clip(t, 1500)}" for n, t in source.items() if n != name) or "(none)"

    async def on_final(self, agent: Agent, items: list[InboxItem], text: str) -> None:
        if not any(i.kind == "instruction" for i in items):
            await self.route_final(agent, items, text)
            return
        await self._answer(agent.name, text)

    async def _answer(self, name: str, text: str) -> None:
        if name not in self.waiting:
            return
        self.waiting.discard(name)
        if self.phase in ("propose", "revise"):
            self.answers[name] = text
            await self.run.board.add("artifact", f"{'Proposal' if self.phase == 'propose' else 'Revision'} from {name}", text, name)
        elif self.phase == "critique":
            self.critiques[name] = text
            await self.run.board.add("note", f"Critique from {name}", text, name)
        elif self.phase == "judge":
            await self.deliver(text, by=name)
            return
        if not self.waiting:
            await self._next()

    async def _next(self) -> None:
        proposers = self.proposers()
        rounds = self.run.limits.debate_rounds
        if self.phase in ("propose", "revise") and len(proposers) > 1 and self.round < rounds:
            self.round += 1
            await self._phase(
                "critique",
                {
                    a.name: "Critique the other proposals below: what's wrong or missing, and what's good. Be brief.\n\n"
                    f"Goal: {self.run.goal}\n\n{self._others(a.name, self.answers)}"
                    for a in proposers
                },
            )
            return
        if self.phase == "critique":
            await self._phase(
                "revise",
                {
                    a.name: "Revise your proposal using these critiques. Reply with your full revised proposal only.\n\n"
                    f"Your proposal:\n{clip(self.answers.get(a.name, ''), 1500)}\n\nCritiques:\n{self._others(a.name, self.critiques)}"
                    for a in proposers
                },
            )
            return
        await self.run.checkpoint("before judging")
        proposals = "\n\n".join(f"### {n}\n{clip(t, 2000)}" for n, t in self.answers.items())
        critiques = "\n\n".join(f"### {n}\n{clip(t, 800)}" for n, t in self.critiques.items())
        await self._phase(
            "judge",
            {
                self.judge().name: f"Goal: {self.run.goal}\n\nPick the best proposal or merge the best parts. "
                "If the goal asks for files, write the final version with write_file. "
                "Reply with the final answer and one paragraph on why.\n\n"
                f"Proposals:\n{proposals}" + (f"\n\nCritiques:\n{critiques}" if critiques else "")
            },
        )

    async def on_error(self, agent: Agent, items: list[InboxItem], error: str) -> None:
        if agent.name in self.waiting and agent is not self.judge():
            await self.run.attention(f"{agent.name} dropped out of the debate: {error}")
            await self._answer(agent.name, f"(no answer: {error})")
            return
        await super().on_error(agent, items, error)

    async def on_restore(self) -> None:
        if not self.phase:
            await self.start()
            return
        for name in sorted(self.waiting):
            await self.run.instruct(name, self.prompts.get(name, f"Continue the {self.phase} step."), sender="debate")

    def state(self) -> dict[str, Any]:
        return {"phase": self.phase, "round": self.round, "waiting": sorted(self.waiting), "answers": self.answers,
                "critiques": self.critiques, "prompts": self.prompts}

    def load_state(self, state: dict[str, Any]) -> None:
        self.phase = state.get("phase", "")
        self.round = state.get("round", 0)
        self.waiting = set(state.get("waiting", []))
        self.answers = dict(state.get("answers", {}))
        self.critiques = dict(state.get("critiques", {}))
        self.prompts = dict(state.get("prompts", {}))
