#!/usr/bin/env python3
"""session-receipt: an itemised till receipt for a Claude Code session.

Roles in one file:

  hook start        SessionStart: opens a log, prunes old ones
  hook prompt       UserPromptSubmit: counts a prompt
  hook tool         PostToolUse (all tools): logs the call and its target
  everything else   the CLI behind /session-receipt:receipt

Logs are JSON lines in plugin data, one file per session. Only tool names,
file paths and the first words of shell commands are kept - never prompt text
or file contents. Fails open: a logging error never touches the session.
"""

import datetime as dt
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

KEEP_DAYS = 30
WIDTH = 34


# --- locations -------------------------------------------------------------


def claude_home():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def project_dir():
    return str(Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve())


def log_dir():
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    path = (Path(base) if base else claude_home() / "plugin-data" / "session-receipt") / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path(session):
    return log_dir() / (re.sub(r"[^\w-]", "_", session) + ".jsonl")


# --- hooks -----------------------------------------------------------------


def append(payload, record):
    session = payload.get("session_id")
    if not isinstance(session, str):
        return
    record["t"] = round(time.time(), 1)
    record["project"] = project_dir()
    with log_path(session).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def target_of(tool, tool_input):
    for key in ("file_path", "notebook_path"):
        if isinstance(tool_input.get(key), str):
            p = Path(tool_input[key])
            try:
                return "file", p.resolve().relative_to(project_dir()).as_posix()
            except ValueError:
                return "file", p.as_posix()
    if tool == "Bash" and isinstance(tool_input.get("command"), str):
        words = tool_input["command"].split()
        return "cmd", " ".join(words[:2])
    return None, None


def hook_start(payload):
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in log_dir().glob("*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
    append(payload, {"e": "start", "source": payload.get("source")})


def hook_prompt(payload):
    append(payload, {"e": "prompt"})


def hook_tool(payload):
    tool = payload.get("tool_name")
    if not isinstance(tool, str):
        return
    kind, target = target_of(tool, payload.get("tool_input") or {})
    record = {"e": "tool", "tool": tool}
    if kind:
        record[kind] = target
    append(payload, record)


# --- reading logs ----------------------------------------------------------


def read_log(path):
    events = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("t"), (int, float)):
                events.append(rec)
    except OSError:
        pass
    return events


def sessions_here():
    """This project's session logs, newest first."""
    here = project_dir()
    found = []
    for f in log_dir().glob("*.jsonl"):
        events = read_log(f)
        if events and events[0].get("project") == here:
            found.append((events[-1]["t"], f, events))
    found.sort(key=lambda x: x[0], reverse=True)
    return found


# --- printing --------------------------------------------------------------


def line(left="", right=""):
    gap = max(1, WIDTH - len(left) - len(right))
    return left + " " * gap + right


def center(text):
    return text.center(WIDTH).rstrip()


def clip(text, n):
    return text if len(text) <= n else "…" + text[-(n - 1):]


def minutes(seconds):
    m = int(round(seconds / 60))
    return "<1 min" if m < 1 else (str(m) + " min" if m < 90 else "%.1f h" % (m / 60))


def receipt(events, path):
    tools = [e for e in events if e.get("e") == "tool"]
    prompts = sum(1 for e in events if e.get("e") == "prompt")
    start, end = events[0]["t"], events[-1]["t"]
    clock = lambda t: dt.datetime.fromtimestamp(t).strftime("%H:%M")

    counts = Counter(e["tool"] for e in tools)
    read = {e["file"] for e in tools if "file" in e and e["tool"] in ("Read", "NotebookRead")}
    changed = {e["file"] for e in tools if "file" in e
               and e["tool"] in ("Edit", "Write", "MultiEdit", "NotebookEdit")}
    handled = Counter(e["file"] for e in tools if "file" in e)
    commands = Counter(e["cmd"] for e in tools if "cmd" in e)
    gaps = [b["t"] - a["t"] for a, b in zip(events, events[1:])]

    rule = "-" * WIDTH
    out = [center("CLAUDE CODE"), center("~ session receipt ~"),
           center(Path(events[0]["project"]).name + " · "
                  + dt.datetime.fromtimestamp(start).strftime("%Y-%m-%d")),
           center("ticket " + path.stem[:8]), rule,
           line("  " + clock(start) + " -> " + clock(end), minutes(end - start) + "  "),
           line("  prompts", str(prompts) + "  "), rule]

    if counts:
        for tool, n in counts.most_common():
            out.append(line("  " + clip(tool, 22), "x" + str(n).rjust(3) + "  "))
    else:
        out.append(center("(no tool calls)"))
    out.append(rule)

    out.append(line("  files read", str(len(read)) + "  "))
    out.append(line("  files changed", str(len(changed)) + "  "))
    if handled:
        name, n = handled.most_common(1)[0]
        out.append("  most handled")
        out.append(line("    " + clip(name, WIDTH - 12), "(" + str(n) + ")  "))
    if commands:
        cmd, n = commands.most_common(1)[0]
        out.append("  favourite command")
        out.append(line("    " + clip(cmd, WIDTH - 12), "(" + str(n) + ")  "))
    if gaps and max(gaps) >= 60:
        out.append(line("  longest pause", minutes(max(gaps)) + "  "))
    out += [rule, line("  TOOL CALLS", str(len(tools)) + "  "), rule, "",
            center("thank you, come again"), ""]

    for f in sorted(changed):
        out.append("  changed: " + f)
    return "\n".join(l.rstrip() for l in out).rstrip()


# --- cli -------------------------------------------------------------------


def die(message):
    print("session-receipt: " + message, file=sys.stderr)
    sys.exit(1)


def cmd_show(rest):
    """`receipt` is this session; `receipt 2` the one before, and so on."""
    found = sessions_here()
    if not found:
        die("no sessions logged for " + project_dir() + " yet")
    n = int(rest[0]) if rest and rest[0].isdigit() else 1
    if not 1 <= n <= len(found):
        die("only " + str(len(found)) + " session(s) logged here")
    _, path, events = found[n - 1]
    print("```\n" + receipt(events, path) + "\n```")


def cmd_list(rest):
    found = sessions_here()
    if not found:
        print("No sessions logged for " + project_dir() + " yet.")
        return
    for i, (_, path, events) in enumerate(found[:15], 1):
        tools = sum(1 for e in events if e.get("e") == "tool")
        when = dt.datetime.fromtimestamp(events[0]["t"]).strftime("%Y-%m-%d %H:%M")
        span = minutes(events[-1]["t"] - events[0]["t"])
        print(str(i).rjust(3) + "  " + when + "  " + span.rjust(7) + "  "
              + str(tools).rjust(4) + " calls  " + path.stem[:8])


USAGE = """session-receipt - an itemised receipt for a session

  [n]     receipt for this session, or the nth most recent here (default 1)
  list    recent sessions in this project
"""


def main(argv):
    if argv[:1] == ["hook"]:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
            {"start": hook_start, "prompt": hook_prompt, "tool": hook_tool}[argv[1]](payload)
        except Exception:
            pass
        return 0
    if argv[:1] and argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[:1] == ["list"]:
        return cmd_list(argv[1:]) or 0
    if argv and not argv[0].isdigit():
        print(USAGE)
        die("unknown command: " + argv[0])
    return cmd_show(argv) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
