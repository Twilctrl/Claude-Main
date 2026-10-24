"""The agent worker: an asyncio task with its own inbox, history and status (DESIGN.md §7).

An agent sleeps until its inbox has something (a task, a direct message, a
user message, an instruction from the mode), then takes a *turn*: it calls its
model, runs the tools the model asks for (up to ``max_tool_steps``), and hands
its final reply to the run, which routes it according to the mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..backends import BackendError, ChatMessage, ChatResult, GenParams, ToolCall
from ..config import Route
from ..context import SUMMARY_PROMPT, Estimator, clip, compact, fit
from ..runtime.board import BoardError
from ..runtime.workspace import SandboxError
from ..toolproto import correction, parse_reply, tools_prompt
from ..tools import Tool, ToolContext, ToolError, ToolResult
from .roles import Role

if TYPE_CHECKING:
    from ..runtime.run import Run

log = logging.getLogger("hive.agent")

MAX_REPROMPTS = 2
STATUSES = ("idle", "thinking", "waiting_backend", "waiting_approval", "paused", "stopped", "error")


class AgentSpec(BaseModel):
    """Everything that defines one agent in a run. Editable mid-run (prompt, params)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    role: str
    backend: str
    model: str | None = None
    fallbacks: list[Route] = Field(default_factory=list)
    description: str = ""
    system_prompt: str
    tools: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    context_budget: int | None = None
    wake_on_broadcast: bool = False
    approval: Literal["ask", "allow", "deny"] = "ask"
    memory: bool | str = True  # False: no long-term memory; a string: use (share) that namespace

    @field_validator("fallbacks", mode="before")
    @classmethod
    def _fb(cls, v: Any) -> Any:
        return [{"backend": x} if isinstance(x, str) else x for x in (v or [])]

    @classmethod
    def from_role(cls, role: Role, *, name: str | None = None, backend: str, model: str | None = None, **overrides: Any) -> AgentSpec:
        data: dict[str, Any] = {
            "name": name or role.name,
            "role": role.name,
            "backend": backend,
            "model": model,
            "description": role.description,
            "system_prompt": role.system_prompt.strip(),
            "tools": list(role.tools),
            "params": dict(role.params),
            "context_budget": role.context_budget,
            "wake_on_broadcast": role.wake_on_broadcast,
            "approval": role.approval,
            "memory": role.memory,
        }
        data.update({k: v for k, v in overrides.items() if v is not None})
        return cls.model_validate(data)

    @property
    def route(self) -> Route:
        return Route(backend=self.backend, model=self.model)


@dataclass
class InboxItem:
    """Something that wakes an agent."""

    kind: Literal["task", "message", "user", "instruction"]
    sender: str
    content: str
    task_id: int | None = None
    depth: int = 0  # reply depth for agent-to-agent messages; see Run.send
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "sender": self.sender, "content": self.content, "task_id": self.task_id, "depth": self.depth}


class AgentStopped(Exception):
    """The agent was stopped (by the user or the loop guard) during a turn."""


class Agent:
    def __init__(self, spec: AgentSpec, run: Run):
        self.spec = spec
        self.run = run
        self.name = spec.name
        self.status = "idle"
        self.status_detail = ""
        self.history: list[ChatMessage] = []
        self.summary = ""
        self.digest: list[str] = []
        self.turns = 0
        self.tokens = 0
        self.protocol_failures = 0  # in a row
        self.current_task: int | None = None
        self.recalled: list[Any] = []  # long-term notes pinned for the current turn
        self.force_text_protocol = False
        self.in_turn = False
        self._pending: list[InboxItem] = []
        self._wake = asyncio.Event()
        self._running = asyncio.Event()
        self._running.set()
        self._stopped = False
        self._loop_task: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[None] | None = None
        bconf = run.app.scheduler.backend(spec.backend).config
        self.est = Estimator(bconf.chars_per_token)

    # Lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop(), name=f"agent-{self.name}")

    async def stop(self) -> None:
        self._stopped = True
        self._running.set()
        self._wake.set()
        me = asyncio.current_task()
        for t in (self._turn_task, self._loop_task):
            if t and not t.done() and t is not me:
                t.cancel()
        for t in (self._turn_task, self._loop_task):
            if t and t is not me:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        await self.set_status("stopped")

    async def pause(self) -> None:
        self._running.clear()
        if not self.in_turn:
            await self.set_status("paused")

    async def resume(self) -> None:
        self._running.set()
        if self.status == "paused":
            await self.set_status("idle")

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def interrupt(self) -> bool:
        """Cancel the turn in progress (and its model call). The agent stays alive."""
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
            return True
        return False

    @property
    def busy(self) -> bool:
        return self.in_turn or bool(self._pending)

    # Inbox ---------------------------------------------------------------------------------

    def deliver(self, item: InboxItem) -> None:
        self._pending.append(item)
        self._wake.set()

    def add_digest(self, line: str) -> None:
        self.digest.append(line)
        del self.digest[:-20]

    async def set_status(self, status: str, detail: str = "") -> None:
        if status == self.status and detail == self.status_detail:
            return
        self.status, self.status_detail = status, detail
        await self.run.emit("agent.status", self.name, status=status, detail=detail)

    async def _loop(self) -> None:
        while not self._stopped:
            while not self._pending and not self._stopped:
                self._wake.clear()
                await self._wake.wait()
            if self._stopped:
                break
            if not self._running.is_set():
                await self.set_status("paused")
                await self._running.wait()
                continue  # the inbox may have changed while paused (a rollback): look again
            items, self._pending = self._pending, []
            blocked = self.run.guard.before_turn(self)
            if blocked:
                await self.run.emit("guard.blocked", self.name, reason=blocked)
                await self.pause()
                self._pending[:0] = items  # keep them for when the user resumes
                continue
            self.in_turn = True
            self._turn_task = asyncio.create_task(self._turn(items), name=f"turn-{self.name}")
            try:
                await self._turn_task
            except asyncio.CancelledError:
                if self._stopped:
                    raise
                await self.run.emit("agent.interrupted", self.name)
            except Exception as e:
                log.exception("agent %s turn failed", self.name)
                await self.run.emit("agent.error", self.name, error=f"{type(e).__name__}: {e}")
                await self.run.on_agent_error(self, items, e)
            finally:
                self.in_turn = False
                self._turn_task = None
            if not self._stopped:
                await self.set_status("paused" if self.paused else "idle")
                self.run.agent_idle(self)

    # The turn -----------------------------------------------------------------------------

    def tools(self) -> list[Tool]:
        return self.run.tools_for(self)

    @property
    def memory_ns(self) -> str | None:
        """This agent's long-term memory namespace, or None when memory is off for it."""
        if not self.run.app.settings.hive.memory.enabled or self.spec.memory is False:
            return None
        return self.spec.memory if isinstance(self.spec.memory, str) and self.spec.memory else self.name

    def native_tools(self) -> bool:
        caps = self.run.app.scheduler.caps(self.spec.route)
        return caps.tool_calling and not self.force_text_protocol

    def budget(self) -> int:
        caps = self.run.app.scheduler.caps(self.spec.route)
        budget = min(self.spec.context_budget or caps.max_context, caps.max_context)
        return max(256, budget - self.max_tokens())

    def max_tokens(self) -> int:
        return int(self.spec.params.get("max_tokens") or self.run.app.settings.hive.max_tokens)

    def system_prompt(self, text_tools: list[Tool] | None) -> str:
        r = self.run
        parts = [self.spec.system_prompt.strip(), "", "## Your situation"]
        parts.append(f'You are "{self.name}" ({self.spec.role}), one of a team of AI agents working together on one goal.')
        parts.append(f"Goal: {r.goal}")
        team = [f"- {a.name} ({a.spec.role}): {a.spec.description}" for a in r.agents.values() if a.name != self.name]
        if team:
            parts.append("Your teammates:\n" + "\n".join(team))
        parts.append("Files live in a shared project folder. Paths are relative to it, like notes/plan.md.")
        guidance = r.mode.guidance(self)
        if guidance:
            parts.append(guidance)
        if self.current_task is not None and self.current_task in r.tasks.tasks:
            t = r.tasks.tasks[self.current_task]
            parts += ["", "## Your current task", f"#{t.id} {t.title}"]
            if t.description:
                parts.append(t.description)
            for d in t.depends_on:
                dep = r.tasks.tasks.get(d)
                if dep and dep.result:
                    parts.append(f"Result of #{dep.id} ({dep.title}): {clip(dep.result, 600)}")
            if t.notes:
                parts.append("Notes: " + " | ".join(t.notes[-3:]))
        if self.recalled:
            cap = int(self.budget() * self.est.chars_per_token * self.run.app.settings.hive.memory.share)
            lines: list[str] = []
            used = 0
            for n in self.recalled:
                line = f"- {n.text}"
                if lines and used + len(line) > cap:
                    break
                lines.append(clip(line, max(80, cap)))
                used += len(line)
            parts += ["", "## From your notes on earlier runs (they may be out of date)", *lines]
        if self.summary:
            parts += ["", "## Your notes from earlier in this run", self.summary]
        if text_tools:
            parts += ["", tools_prompt(text_tools)]
        return "\n".join(parts)

    def render_trigger(self, items: list[InboxItem]) -> str:
        lines = []
        if self.digest:
            lines.append("[Team channel since your last turn]")
            lines += [f"- {d}" for d in self.digest]
            lines.append("")
            self.digest = []
        for it in items:
            if it.kind == "task":
                lines.append(f"[Task #{it.task_id} from {it.sender}]\n{it.content}")
            elif it.kind == "message":
                lines.append(f"[Message from {it.sender}]\n{it.content}")
            elif it.kind == "user":
                lines.append(f"[Message from the user]\n{it.content}")
            else:
                lines.append(f"[{it.sender}]\n{it.content}")
        return "\n\n".join(lines).strip()

    async def _turn(self, items: list[InboxItem]) -> None:
        self.run.activity()
        self.turns += 1
        self.run.turns_used += 1
        for it in items:
            if it.task_id is not None:
                self.current_task = it.task_id
                try:
                    await self.run.tasks.start(it.task_id, self.name)
                except BoardError:
                    pass
        trigger = self.render_trigger(items)
        self.history.append(ChatMessage("user", trigger))
        await self.run.emit("agent.turn", self.name, turn=self.turns, trigger=clip(trigger, 4000), items=[i.to_dict() for i in items])
        await self._recall(trigger)
        await self.set_status("thinking")

        final: str | None = None
        reprompts = 0
        steps = self.run.limits.max_tool_steps
        step = 0
        while step <= steps:
            await self._wait_if_paused()
            tools = self.tools()
            native = self.native_tools() and bool(tools)
            system = self.system_prompt(None if native else tools)
            await self._compact(system)
            messages, _ = fit(system, self.history, self.budget(), self.est)
            try:
                result = await self._call_model(messages, tools if native else None)
            except BackendError as e:
                if e.code == "tools_unsupported" and native:
                    self.force_text_protocol = True
                    await self.run.emit("agent.protocol", self.name, detail="model has no native tools; using the text protocol")
                    continue
                raise
            step += 1

            if native:
                if result.tool_calls:
                    calls = [ToolCall(c.name, c.arguments, c.id or f"call_{uuid.uuid4().hex[:10]}") for c in result.tool_calls]
                    self.history.append(ChatMessage("assistant", result.text, tool_calls=calls))
                    for call in calls:
                        res = await self._exec_tool(call.name, call.arguments)
                        self.history.append(ChatMessage("tool", self._clip_tool(res.output), tool_call_id=call.id, name=call.name))
                        if res.end_turn:
                            final = res.output
                    if final is not None:
                        break
                    continue
                final = result.text.strip()
                self.history.append(ChatMessage("assistant", result.text))
                break

            parsed = parse_reply(result.text, {t.name for t in tools})
            self.history.append(ChatMessage("assistant", result.text))
            if parsed.kind == "tool":
                self.protocol_failures = 0
                res = await self._exec_tool(parsed.tool, parsed.args)
                self.history.append(ChatMessage("user", f"[{parsed.tool} result]\n{self._clip_tool(res.output)}"))
                if res.end_turn:
                    final = res.output
                    break
                continue
            if parsed.kind in ("final", "text"):
                self.protocol_failures = 0
                final = parsed.text
                break
            # A reply that tried and failed to follow the protocol.
            self.protocol_failures += 1
            await self.run.emit("agent.protocol", self.name, detail=parsed.error, failures=self.protocol_failures)
            self.run.guard.on_protocol_failure(self)
            if reprompts < MAX_REPROMPTS:
                reprompts += 1
                self.history.append(ChatMessage("user", correction(parsed.error, tools)))
                continue
            final = result.text.strip()
            break
        else:
            final = f"(stopped after {steps} tool calls in one turn)"
            await self.run.emit("guard.trigger", self.name, rule="max_tool_steps", action="end_turn", detail=final)

        final = final or "(no reply)"
        await self.run.emit("agent.reply", self.name, text=clip(final, 8000))
        self.run.guard.after_reply(self, final)
        await self.run.on_final(self, items, final)

    async def _recall(self, trigger: str) -> None:
        """Pin the most relevant long-term notes for this turn."""
        self.recalled = []
        cfg = self.run.app.settings.hive.memory
        if not self.memory_ns or cfg.recall <= 0:
            return
        query = [trigger, self.run.goal]
        t = self.run.tasks.tasks.get(self.current_task) if self.current_task is not None else None
        if t:
            query += [t.title, t.description]
        notes = await self.run.recall(self, " ".join(query), limit=cfg.recall)
        if notes:
            self.recalled = notes
            await self.run.emit("memory.recalled", self.name, notes=[{"id": n.id, "text": clip(n.text, 300), "score": n.score} for n in notes])

    async def reflect(self, outcome: str) -> list[str]:
        """One model call at the end of a run: up to three lessons for next time."""
        from ..context import transcript
        from ..memory import REFLECT_PROMPT, parse_lessons

        room = int(self.budget() * self.est.chars_per_token * 0.6)
        seen = (f"Your notes:\n{self.summary}\n\n" if self.summary else "") + transcript(self.history, room)
        msgs = [
            ChatMessage("system", f'You are "{self.name}" ({self.spec.role}). {self.spec.description}'.strip()),
            ChatMessage("user", f"What happened, as you saw it:\n{seen}\n\n{REFLECT_PROMPT.format(goal=self.run.goal, outcome=outcome)}"),
        ]
        res = await self.run.app.scheduler.chat(
            self.spec.route, msgs, params=GenParams(temperature=0.2, max_tokens=200),
            fallbacks=self.spec.fallbacks or self.run.app.scheduler.backend(self.spec.backend).config.fallbacks,
        )
        await self._record_call(res, ok=True, purpose="reflect")
        return parse_lessons(res.text)

    async def _wait_if_paused(self) -> None:
        if not self._running.is_set():
            await self.set_status("paused")
            await self._running.wait()
            await self.set_status("thinking")
        if self._stopped:
            raise AgentStopped()
        stop = self.run.guard.check_budget()
        if stop:
            raise AgentStopped(stop)

    def _clip_tool(self, text: str) -> str:
        max_chars = max(600, int(self.budget() * self.est.chars_per_token * 0.25))
        return clip(text, max_chars)

    async def _compact(self, system: str) -> None:
        async def summarize(old: str, transcript: str, max_tokens: int) -> str:
            words = max(40, int(max_tokens * 0.7))
            msgs = [
                ChatMessage("system", SUMMARY_PROMPT.format(words=words)),
                ChatMessage("user", f"Earlier summary:\n{old or '(none)'}\n\nNew transcript:\n{transcript}"),
            ]
            route = self.run.app.settings.hive.summarizer or self.spec.route
            res = await self.run.app.scheduler.chat(route, msgs, params=GenParams(temperature=0.1, max_tokens=max_tokens))
            await self._record_call(res, ok=True, purpose="summary")
            return res.text

        new_history, new_summary, how = await compact(system, self.history, self.summary, self.budget(), self.est, summarize)
        if how != "none":
            dropped = len(self.history) - len(new_history)
            self.history, self.summary = new_history, new_summary
            await self.run.emit("agent.context", self.name, how=how, dropped=dropped, summary=clip(new_summary, 2000))

    async def _call_model(self, messages: list[ChatMessage], tools: list[Tool] | None) -> ChatResult:
        params = GenParams.from_dict({**self.spec.params, "max_tokens": self.max_tokens()})
        run = self.run

        def on_delta(text: str) -> None:
            run.live("agent.delta", self.name, text=text)

        def on_event(kind: str, data: dict[str, Any]) -> None:
            if kind == "queued":
                run.spawn(self.set_status("waiting_backend", f"queued on {data['backend']}"))
            elif kind == "started":
                run.spawn(self.set_status("thinking", f"{data['backend']}/{data['model']}"))
            elif kind == "waiting_backend":
                run.spawn(self.set_status("waiting_backend", f"{', '.join(data['backends'])} down"))
            if kind == "stream_reset":
                run.live("agent.stream_reset", self.name, **data)
            elif kind in ("rerouted", "waiting_backend", "retry"):
                run.spawn(run.emit(f"model.{kind}", self.name, **data))

        start = time.monotonic()
        try:
            result = await run.app.scheduler.chat(
                self.spec.route,
                messages,
                [t.spec() for t in tools] if tools else None,
                params=params,
                fallbacks=self.spec.fallbacks or run.app.scheduler.backend(self.spec.backend).config.fallbacks,
                on_delta=on_delta,
                on_event=on_event,
            )
        except BackendError as e:
            await run.app.store.add_model_call(run.id, self.name, self.spec.backend, self.spec.model or "", None, None,
                                               (time.monotonic() - start) * 1000, False, str(e))
            await run.emit("model.call", self.name, ok=False, backend=self.spec.backend, error=str(e))
            raise
        chars = sum(len(m.content) for m in messages)
        self.est.calibrate(chars, result.usage.prompt_tokens)
        await self._record_call(result, ok=True)
        return result

    async def _record_call(self, result: ChatResult, ok: bool, purpose: str = "turn") -> None:
        used = (result.usage.prompt_tokens or 0) + (result.usage.completion_tokens or 0)
        self.tokens += used
        self.run.tokens_used += used
        await self.run.app.store.add_model_call(
            self.run.id, self.name, result.backend, result.model, result.usage.prompt_tokens,
            result.usage.completion_tokens, result.total_seconds * 1000, ok, None,
        )
        await self.run.emit(
            "model.call",
            self.name,
            ok=ok,
            purpose=purpose,
            backend=result.backend,
            model=result.model,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            seconds=round(result.total_seconds, 2),
            tok_s=round(result.tokens_per_sec, 1) if result.tokens_per_sec else None,
            tool_calls=len(result.tool_calls),
        )

    async def _exec_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        allowed = {t.name: t for t in self.tools()}
        t = allowed.get(name)
        if t is None:
            self.run.guard.on_protocol_failure(self)
            res = ToolResult(f"Error: you have no tool named {name!r}. Your tools: {', '.join(sorted(allowed)) or 'none'}.", ok=False)
            await self.run.emit("tool.result", self.name, tool=name, ok=False, output=res.output)
            return res
        try:
            args = t.validate(args)
        except ToolError as e:
            self.run.guard.on_protocol_failure(self)
            res = ToolResult(f"Error: {e}", ok=False)
            await self.run.emit("tool.result", self.name, tool=name, ok=False, output=res.output)
            return res
        ctx = ToolContext(self.run, self)
        shown_args = {k: clip(v, 2000) if isinstance(v, str) else v for k, v in args.items()}
        if t.is_risky(ctx, args):
            decision = await self.run.approvals.request(self, name, args)
            if not decision.approved:
                why = f": {decision.reason}" if decision.reason else ""
                res = ToolResult(f"The user did not allow this {name} call{why}. Don't retry it; do something else.", ok=False)
                await self.run.emit("tool.result", self.name, tool=name, ok=False, output=res.output, rejected=True)
                return res
        await self.run.emit("tool.call", self.name, tool=name, args=shown_args)
        self.run.guard.on_tool_call(self, name, args)
        try:
            res = await t(ctx, args)
        except (ToolError, SandboxError, BoardError) as e:
            res = ToolResult(f"Error: {e}", ok=False)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a tool bug mustn't kill the agent
            log.exception("tool %s failed", name)
            res = ToolResult(f"Error: {type(e).__name__}: {e}", ok=False)
        await self.run.emit(
            "tool.result", self.name, tool=name, ok=res.ok, output=clip(res.output, 4000), data=res.data, progress=res.progress
        )
        if res.progress:
            self.run.guard.on_progress(self.name)
        return res

    # State for checkpoints and restarts ----------------------------------------------------------

    def state(self) -> dict[str, Any]:
        return {
            "history": [m.to_dict() for m in self.history],
            "summary": self.summary,
            "digest": self.digest,
            "turns": self.turns,
            "tokens": self.tokens,
            "current_task": self.current_task,
            "force_text_protocol": self.force_text_protocol,
            "pending": [i.to_dict() for i in self._pending],
        }

    def load_state(self, s: dict[str, Any]) -> None:
        self.history = [ChatMessage.from_dict(m) for m in s.get("history", [])]
        self.summary = s.get("summary", "")
        self.digest = list(s.get("digest", []))
        self.turns = s.get("turns", 0)
        self.tokens = s.get("tokens", 0)
        self.current_task = s.get("current_task")
        self.force_text_protocol = s.get("force_text_protocol", False)
        self._pending = [InboxItem(**i) for i in s.get("pending", [])]
        if self._pending:
            self._wake.set()


def short_json(v: Any, n: int = 200) -> str:
    return clip(json.dumps(v, ensure_ascii=False), n)
