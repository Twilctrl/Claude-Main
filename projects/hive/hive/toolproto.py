"""The text tool protocol, for models without native tool calling (DESIGN.md §6).

The model is told to reply with exactly one JSON object per turn::

    {"tool": "read_file", "args": {"path": "notes.txt"}}
    {"final": "what I did"}

Parsing is forgiving because small models are sloppy: fenced or bare JSON,
the first balanced object in the text, trailing commas, Python-style
literals, and common synonyms for the keys are all accepted.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .tools import Tool

_TOOL_KEYS = ("tool", "name", "function", "action", "tool_name")
_ARGS_KEYS = ("args", "arguments", "parameters", "input", "params")
_FINAL_KEYS = ("final", "answer", "final_answer", "result", "done", "response")


@dataclass
class Parsed:
    kind: Literal["tool", "final", "text", "error"]
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    error: str = ""


def tools_prompt(tools: list[Tool]) -> str:
    """The tool list and reply contract, for the system prompt."""
    lines = ["## Tools", "You can use these tools, one per reply:"]
    for t in tools:
        lines.append(f"- {t.signature()}: {t.description}")
    example = tools[0].name if tools else "read_file"
    lines += [
        "",
        "To use a tool, reply with ONLY a JSON object, nothing else:",
        f'{{"tool": "{example}", "args": {{...}}}}',
        "You will get the tool's result, then you can use another tool.",
        "When you have finished, reply with ONLY:",
        '{"final": "a short summary of what you did"}',
    ]
    return "\n".join(lines)


def correction(error: str, tools: list[Tool]) -> str:
    """The short message sent back after a reply that didn't follow the protocol."""
    example = tools[0].name if tools else "read_file"
    return (
        f"Your reply couldn't be used: {error}\n"
        f'Reply with ONLY one JSON object: {{"tool": "{example}", "args": {{...}}}} to use a tool, '
        f'or {{"final": "..."}} when you are done.'
    )


def _balanced_objects(text: str) -> list[str]:
    """Every top-level {...} span in ``text``, respecting strings."""
    out, depth, start, in_str, esc, quote = [], 0, -1, False, False, ""
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in "\"'" and depth > 0:
            in_str, quote = True, ch
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                out.append(text[start : i + 1])
    return out


def _loads(s: str) -> Any:
    s = s.strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    fixed = re.sub(r",\s*([}\]])", r"\1", s)  # trailing commas
    try:
        return json.loads(fixed)
    except ValueError:
        pass
    try:  # single quotes, True/False/None
        py = re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", re.sub(r"\bnull\b", "None", fixed)))
        val = ast.literal_eval(py)
        return val if isinstance(val, dict) else None
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None


def extract_json(text: str) -> dict[str, Any] | None:
    """The first JSON object in ``text`` (fenced, bare, or embedded in prose)."""
    candidates = [m.group(1) for m in re.finditer(r"```(?:json|JSON)?\s*(\{.*?\})\s*```", text, re.S)]
    candidates += [text]
    candidates += _balanced_objects(text)
    for c in candidates:
        val = _loads(c)
        if isinstance(val, dict):
            return val
    return None


def _looks_like_attempt(text: str) -> bool:
    return bool(re.search(r'[{]\s*["\']?(tool|final|name|args)["\']?\s*:', text))


def parse_reply(text: str, tool_names: set[str]) -> Parsed:
    """Interpret a model's text reply under the protocol."""
    obj = extract_json(text)
    if obj is None:
        if _looks_like_attempt(text):
            return Parsed("error", text=text, error="it looks like JSON but isn't valid JSON")
        return Parsed("text", text=text.strip())
    for k in _FINAL_KEYS:
        if k in obj and not any(t in obj for t in _TOOL_KEYS if t != "name"):
            val = obj[k]
            return Parsed("final", text=val if isinstance(val, str) else json.dumps(val))
    name = next((obj[k] for k in _TOOL_KEYS if isinstance(obj.get(k), str)), None)
    if isinstance(obj.get("function"), dict):  # {"function": {"name": ..., "arguments": ...}}
        fn = obj["function"]
        name, obj = fn.get("name"), {"args": fn.get("arguments", {})}
    if not name:
        return Parsed("error", text=text, error='the JSON has no "tool" or "final" key')
    if name not in tool_names:
        avail = ", ".join(sorted(tool_names)) or "none"
        return Parsed("error", tool=name, text=text, error=f"there is no tool named {name!r} (you have: {avail})")
    args: Any = next((obj[k] for k in _ARGS_KEYS if k in obj), None)
    if args is None:
        args = {k: v for k, v in obj.items() if k not in _TOOL_KEYS}
    if isinstance(args, str):
        args = _loads(args) if args.strip() else {}
    if not isinstance(args, dict):
        return Parsed("error", tool=name, text=text, error='"args" must be a JSON object')
    return Parsed("tool", tool=name, args=args, text=text)
