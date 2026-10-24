"""A backend with no model behind it, for tests, demos and UI work.

The model name picks the behaviour:

``echo``
    Replies with the last user message.
``fake`` (default)
    A small rule-based stand-in that plays Hive's built-in roles well enough
    to drive a whole run (see :mod:`hive.backends.fake`).
any key of ``options.script``
    Plays that list of replies in order. A reply is a string (text), a mapping
    ``{tool: name, args: {...}}`` (one tool call), or ``{text: ..., tool_calls: [...]}``.

Options (all optional): ``latency`` (seconds before the first token),
``tokens_per_sec`` (0 streams instantly), ``fail_calls`` (1-based call numbers
that fail with ``fail_status``, default 500), ``hang_calls`` (calls that never
produce a token, for timeout tests), ``down`` (refuse everything).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .base import (
    BackendError,
    BackendUnavailable,
    ChatEvent,
    ChatMessage,
    ChatResult,
    GenParams,
    Health,
    ModelBackend,
    ModelInfo,
    Stopwatch,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
)


@dataclass
class MockReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


def _reply_from_item(item: Any) -> MockReply:
    if isinstance(item, str):
        return MockReply(text=item)
    if isinstance(item, dict):
        if "tool" in item:
            return MockReply(text=item.get("text", ""), tool_calls=[ToolCall(item["tool"], item.get("args") or {})])
        calls = [ToolCall(c["tool"] if "tool" in c else c["name"], c.get("args", c.get("arguments")) or {}) for c in item.get("tool_calls", [])]
        return MockReply(text=item.get("text", ""), tool_calls=calls)
    raise ValueError(f"bad mock script item: {item!r}")


class MockBackend(ModelBackend):
    type_name = "mock"

    def __init__(self, config: Any, client: Any = None):
        super().__init__(config, client)
        opts = config.options
        self.down: bool = bool(opts.get("down", False))
        self.latency: float = float(opts.get("latency", 0.0))
        self.tokens_per_sec: float = float(opts.get("tokens_per_sec", 0.0))
        self.fail_calls: set[int] = set(opts.get("fail_calls", []))
        self.fail_status: int = int(opts.get("fail_status", 500))
        self.hang_calls: set[int] = set(opts.get("hang_calls", []))
        self.script: dict[str, list[Any]] = {k: list(v) for k, v in (opts.get("script") or {}).items()}
        self._pos: dict[str, int] = {}
        self.calls = 0
        self.received: list[dict[str, Any]] = []  # what each call was given, for tests

    def reply_for(self, messages: list[ChatMessage], tools: list[ToolSpec] | None, model: str, native: bool) -> MockReply:
        if model in self.script:
            i = self._pos.get(model, 0)
            self._pos[model] = i + 1
            items = self.script[model]
            return _reply_from_item(items[i]) if i < len(items) else MockReply(text="[script finished]")
        if model == "echo":
            last = next((m.content for m in reversed(messages) if m.role == "user"), "")
            return MockReply(text=f"echo: {last}")
        from .fake import fake_reply

        return fake_reply(messages, tools, native)

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        stream: bool = False,
        *,
        model: str | None = None,
        params: GenParams | None = None,
    ) -> AsyncIterator[ChatEvent]:
        model = model or self.config.default_model or "fake"
        self.calls += 1
        n = self.calls
        self.received.append({"messages": list(messages), "tools": tools, "model": model, "params": params})
        clock = Stopwatch()
        if self.down:
            raise BackendUnavailable(f"{self.name}: connection refused (mock is down)")
        if self.latency:
            await asyncio.sleep(self.latency)
        if n in self.hang_calls:
            await asyncio.sleep(3600)
        if n in self.fail_calls:
            raise BackendError(
                f"{self.name}: HTTP {self.fail_status}: injected failure on call {n}",
                retryable=self.fail_status == 429 or self.fail_status >= 500,
                status=self.fail_status,
            )

        native = tools is not None and self.capabilities_for(model).tool_calling
        reply = self.reply_for(messages, tools, model, native)
        calls = reply.tool_calls
        text = reply.text
        if calls and not native:
            # No native tools: answer in the JSON text protocol, one action per reply.
            c = calls[0]
            text = json.dumps({"tool": c.name, "args": c.arguments})
            calls = []

        pieces = re.findall(r"\S+\s*|\s+", text) or []
        delay = 1.0 / self.tokens_per_sec if self.tokens_per_sec > 0 else 0.0
        for p in pieces:
            clock.tick()
            if delay:
                await asyncio.sleep(delay)
            if stream:
                yield TextDelta(p)
        clock.tick()
        prompt_chars = sum(len(m.content) for m in messages)
        yield ChatResult(
            text=text,
            tool_calls=calls,
            usage=Usage(int(prompt_chars / self.config.chars_per_token), max(1, len(pieces))),
            model=model,
            backend=self.name,
            total_seconds=clock.total,
            gen_seconds=clock.gen if pieces else None,
            finish_reason="tool_calls" if calls else "stop",
        )

    async def list_models(self) -> list[ModelInfo]:
        if self.down:
            raise BackendUnavailable(f"{self.name}: connection refused (mock is down)")
        names = ["fake", "echo", *self.script]
        return [ModelInfo(name=n, context=self.config.context) for n in dict.fromkeys(names)]

    async def health(self) -> Health:
        if self.down:
            return Health(ok=False, detail="mock is down")
        return Health(ok=True, latency_ms=0.1, detail="mock")
