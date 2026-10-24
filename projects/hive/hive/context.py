"""Fitting an agent's conversation into its model's context window (DESIGN.md §5).

Token counts are estimates (characters ÷ chars_per_token), corrected by the
prompt-token counts the server reports. When the history doesn't fit, the
oldest turns are folded into a rolling summary by a model call, or, when the
budget is tiny or summarising fails, simply dropped.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .backends import ChatMessage

log = logging.getLogger("hive.context")

#: Below this budget (tokens), summarising costs more than it saves: just truncate.
MIN_SUMMARY_BUDGET = 2000
#: Turns always kept verbatim at the end of the history.
KEEP_RECENT = 4

Summarize = Callable[[str, str, int], Awaitable[str]]  # (old summary, transcript, max tokens) -> summary


def clip(text: str, max_chars: int) -> str:
    """Keep the head and tail of ``text`` if it's longer than ``max_chars``."""
    if len(text) <= max_chars:
        return text
    avail = max(20, max_chars - 40)  # room for the marker
    head = int(avail * 0.65)
    tail = avail - head
    cut = len(text) - head - tail
    return f"{text[:head]}\n[... {cut} characters cut ...]\n{text[-tail:]}"


@dataclass
class Estimator:
    """Characters per token, corrected from what the server reports."""

    chars_per_token: float = 3.5

    def tokens(self, text: str) -> int:
        return int(len(text) / self.chars_per_token) + 1

    def message_tokens(self, m: ChatMessage) -> int:
        extra = sum(len(json.dumps(c.arguments)) + len(c.name) for c in m.tool_calls)
        return int((len(m.content) + extra) / self.chars_per_token) + 4

    def calibrate(self, chars: int, reported_tokens: int | None) -> None:
        if not reported_tokens or reported_tokens < 50 or chars < 200:
            return
        observed = chars / reported_tokens
        if 1.0 < observed < 8.0:
            self.chars_per_token = 0.8 * self.chars_per_token + 0.2 * observed


def _blocks(history: list[ChatMessage]) -> list[list[ChatMessage]]:
    """Group messages so an assistant tool call stays with its tool results."""
    blocks: list[list[ChatMessage]] = []
    for m in history:
        if m.role == "tool" and blocks and (blocks[-1][0].tool_calls or blocks[-1][-1].role == "tool"):
            blocks[-1].append(m)
        else:
            blocks.append([m])
    return blocks


def fit(system: str, history: list[ChatMessage], budget: int, est: Estimator) -> tuple[list[ChatMessage], int]:
    """The messages to send: the system prompt plus as many recent turns as fit.

    Returns (messages, number of history messages left out). The newest turn is
    always kept, clipped if it alone is too big.
    """
    sys_msg = ChatMessage("system", system)
    room = budget - est.message_tokens(sys_msg)
    kept: list[list[ChatMessage]] = []
    for block in reversed(_blocks(history)):
        cost = sum(est.message_tokens(m) for m in block)
        if cost > room:
            if not kept:  # the newest turn alone doesn't fit: clip it
                max_chars = max(200, int(room * est.chars_per_token) - 50)
                block = [ChatMessage(m.role, clip(m.content, max_chars // len(block)), m.tool_calls, m.tool_call_id, m.name) for m in block]
                kept.append(block)
            break
        kept.append(block)
        room -= cost
    msgs = [m for b in reversed(kept) for m in b]
    while msgs and msgs[0].role == "tool":  # never start on an orphaned tool result
        msgs.pop(0)
    return [sys_msg, *msgs], len(history) - len(msgs)


def transcript(messages: list[ChatMessage], max_chars: int) -> str:
    lines = []
    for m in messages:
        if m.tool_calls:
            calls = "; ".join(f"{c.name}({json.dumps(c.arguments)[:200]})" for c in m.tool_calls)
            lines.append(f"{m.role}: {m.content}\n  [called {calls}]".strip())
        else:
            who = f"tool {m.name}" if m.role == "tool" else m.role
            lines.append(f"{who}: {clip(m.content, 1500)}")
    return clip("\n".join(lines), max_chars)


async def compact(
    system: str,
    history: list[ChatMessage],
    summary: str,
    budget: int,
    est: Estimator,
    summarize: Summarize | None,
) -> tuple[list[ChatMessage], str, str]:
    """Shrink ``history`` so it fits ``budget`` alongside ``system``.

    Returns (new history, new summary, how: "none" | "summarized" | "truncated").
    """
    _, dropped = fit(system, history, budget, est)
    if dropped == 0:
        return history, summary, "none"
    # Fold a little more than the overflow, so we don't summarise every turn.
    blocks = _blocks(history)
    keep_from = len(blocks)
    tokens_kept = 0
    target = int(budget * 0.6)
    for i in range(len(blocks) - 1, -1, -1):
        cost = sum(est.message_tokens(m) for m in blocks[i])
        if tokens_kept + cost > target and len(blocks) - i > 1:
            break
        tokens_kept += cost
        keep_from = i
    keep_from = min(keep_from, max(0, len(blocks) - 1))
    old = [m for b in blocks[:keep_from] for m in b]
    new_history = [m for b in blocks[keep_from:] for m in b]
    if not old:
        return history, summary, "none"
    if summarize is None or budget < MIN_SUMMARY_BUDGET:
        return new_history, summary, "truncated"
    max_summary = max(100, int(budget * 0.15))
    try:
        text = await summarize(summary, transcript(old, int(budget * est.chars_per_token)), max_summary)
        text = clip(text.strip(), int(max_summary * est.chars_per_token))
        if not text:
            raise ValueError("empty summary")
        return new_history, text, "summarized"
    except Exception as e:  # any failure: fall back to dropping
        log.info("summary failed, truncating instead: %s", e)
        return new_history, summary, "truncated"


SUMMARY_PROMPT = (
    "You condense a teammate's working notes. Merge the earlier summary and the new transcript into one "
    "short summary written as notes to yourself. Keep: decisions, file names, task numbers and status, "
    "facts learned, open questions, what you were about to do next. Drop pleasantries and repeated text. "
    "At most {words} words. Reply with the summary only."
)
