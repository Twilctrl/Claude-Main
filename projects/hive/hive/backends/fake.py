"""The ``fake`` mock model: a rule-based stand-in for an LLM.

It reads the conversation the way a cooperative small model would and plays
Hive's built-in roles well enough to drive whole runs, with no model at all.
It takes one action per reply, so it works with native tool calls and with
the text protocol alike. It is deliberately simple and deterministic:

- planner: one ``create_task`` per file named in the goal, then a summary
- coder / writer: ``write_file`` the target file (reading it first if asked)
- reviewer: ``request_changes`` on "round 1", ``approve`` after that
- tester: ``approve``
- coordinator: delegates one task per teammate, then ``finish_run`` when done
- judge: writes the chosen answer to a file if the goal names one
- anyone asked to propose, critique or revise in a debate: a short text answer
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from .base import ChatMessage, ToolCall, ToolSpec

if TYPE_CHECKING:
    from .mock import MockReply

_FILE = re.compile(r"(?<![\w/.-])([\w-]+(?:/[\w-]+)*\.(?:py|txt|md|js|ts|html|css|json|ya?ml|sh|toml|csv|rs|go|c|h))\b")
_RESULT = re.compile(r"^\[(\w+) result\]")


def _role(system: str) -> str:
    m = re.search(r'You are "[^"]+" \(([\w-]+)\)', system) or re.search(r"You are the (\w+)", system)
    return m.group(1).lower() if m else "assistant"


def _tool_names(system: str) -> set[str]:
    if "## Tools" not in system:
        return set()
    section = system.split("## Tools", 1)[1]
    return set(re.findall(r"^- (\w+)\(", section, re.M))


def _goal(system: str) -> str:
    m = re.search(r"^Goal: (.+)$", system, re.M)
    return m.group(1).strip() if m else ""


def _teammates(system: str) -> list[tuple[str, str]]:
    m = re.search(r"Your teammates:\n((?:- .+\n?)+)", system)
    if not m:
        return []
    return re.findall(r"^- ([\w-]+) \(([\w-]+)\)", m.group(1), re.M)


def _turn(messages: list[ChatMessage]) -> tuple[str, list[tuple[str, dict[str, Any]]], str]:
    """(trigger text, tool calls made this turn, last tool result)."""
    start = 0
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.role == "user" and not _RESULT.match(m.content) and not m.content.startswith(("Your reply couldn't", "[Hive]")):
            start = i
            break
    trigger = messages[start].content if messages else ""
    calls: list[tuple[str, dict[str, Any]]] = []
    last_result = ""
    for m in messages[start + 1 :]:
        if m.role == "assistant":
            if m.tool_calls:
                calls += [(c.name, c.arguments) for c in m.tool_calls]
            else:
                try:
                    obj = json.loads(m.content)
                    if isinstance(obj, dict) and "tool" in obj:
                        calls.append((obj["tool"], obj.get("args") or {}))
                except ValueError:
                    pass
        elif m.role == "tool" or (m.role == "user" and _RESULT.match(m.content)):
            last_result = m.content
    return trigger, calls, last_result


def _content_for(path: str, goal: str, role: str, revised: bool) -> str:
    note = "Revised after review.\n" if revised else ""
    if path.endswith(".py"):
        return (
            f'"""{goal}"""\n'
            + (f"# {note}" if note else "")
            + "\n\ndef main() -> None:\n"
            + f'    print("Hello from Hive: {goal[:40].replace(chr(34), "")}")\n\n\n'
            + 'if __name__ == "__main__":\n    main()\n'
        )
    if path.endswith((".md", ".txt")):
        title = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()
        return f"# {title}\n\n{goal}\n\n{note}Written by the {role}.\n"
    if path.endswith(".json"):
        return json.dumps({"goal": goal, "revised": revised}, indent=2) + "\n"
    return f"{goal}\n{note}"


def fake_reply(messages: list[ChatMessage], tools: list[ToolSpec] | None, native: bool) -> "MockReply":
    from .mock import MockReply

    def call(name: str, **args: Any) -> MockReply:
        return MockReply(tool_calls=[ToolCall(name, args)])

    def final(text: str) -> MockReply:
        return MockReply(text=text if native or not tools else text)

    system = messages[0].content if messages and messages[0].role == "system" else ""
    available = {t.name for t in tools} if tools else _tool_names(system)
    text_protocol = not tools and bool(available)
    role = _role(system)
    goal = _goal(system)
    trigger, calls, last = _turn(messages)
    called = [c[0] for c in calls]

    def done(text: str) -> MockReply:
        return MockReply(text=json.dumps({"final": text})) if text_protocol else MockReply(text=text)

    files = _FILE.findall(trigger) or _FILE.findall(goal)
    lower = trigger.lower()

    # Debate prompts are the same for every role.
    if "propose" in lower and "critique" not in lower:
        return done(f"Proposal from the {role}: do the simplest thing that meets the goal ({goal[:80]}), in one file.")
    if "critique" in lower:
        return done("Critique: the proposals are similar; the first is simplest. The second handles edge cases better.")
    if "revise" in lower:
        return done(f"Revised proposal from the {role}: keep it simple, and handle empty input.")

    if role == "planner":
        targets = files[:3] or ["README.md"]
        made = called.count("create_task")
        if "create_task" in available and made < len(targets):
            f = targets[made]
            return call("create_task", title=f"Write {f}", description=f"Create {f} for this goal: {goal}")
        return done("Plan: " + "; ".join(f"write {f}" for f in targets))

    if role in ("coder", "writer"):
        target = files[0] if files else ("main.py" if role == "coder" else "notes.md")
        revised = "changes" in lower or "review" in lower or "fix" in lower
        if "write_file" in available:
            if last and ("haven't read it" in last or "after you read it" in last):
                return call("read_file", path=target)
            if called and called[-1] == "read_file" or "write_file" not in called:
                return call("write_file", path=target, content=_content_for(target, goal, role, revised))
        return done(f"Wrote {target}.")

    if role in ("reviewer", "tester"):
        if "list_files" in available and "list_files" not in called:
            return call("list_files")
        listed = _FILE.findall(last) if called and called[-1] == "list_files" else []
        if "read_file" in available and "read_file" not in called and listed:
            return call("read_file", path=listed[0])
        if role == "reviewer" and "round 1 of" in lower and "request_changes" in available:
            return call("request_changes", notes="1. Add a line saying what the file is for.")
        if "approve" in available:
            return call("approve", notes=f"The {role} checked the files; they do what the task asks.")
        return done("Looks good.")

    if role == "coordinator":
        mates = [n for n, r in _teammates(system) if r not in ("coordinator", "judge")]
        made = called.count("create_task")
        if "finish_run" in available and ("all tasks are done" in lower or "every task is done" in lower):
            return call("finish_run", summary=f"All tasks are done for: {goal}")
        if "create_task" in available and not any(w in lower for w in ("finished", "is done", "failed")) and made < len(mates):
            name = mates[made]
            target = files[made] if made < len(files) else f"{name}.md"
            return call("create_task", title=f"Write {target}", description=f"Create {target} for: {goal}", assign_to=name)
        return done("Delegated the work; waiting for results." if made else "Noted.")

    if role == "judge":
        if files and "write_file" in available and "write_file" not in called:
            return call("write_file", path=files[0], content=_content_for(files[0], goal, "judge", False))
        return done("I pick the first proposal: it is the simplest that meets the goal.")

    return done(f"(fake model) Done: {trigger.splitlines()[-1][:120] if trigger else 'ok'}")
