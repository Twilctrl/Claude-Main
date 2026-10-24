"""The ``fake`` mock model: a rule-based stand-in for an LLM.

It reads the conversation and replies the way a cooperative small model would,
so runs can be exercised end to end without a real model. It is deliberately
dumb: it only knows the built-in tools and roles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import ChatMessage, ToolSpec

if TYPE_CHECKING:
    from .mock import MockReply


def fake_reply(messages: list[ChatMessage], tools: list[ToolSpec] | None, native: bool) -> "MockReply":
    from .mock import MockReply

    last = next((m.content for m in reversed(messages) if m.role == "user"), "")
    return MockReply(text=f"(fake model) I read your message: {last[:200]}")
