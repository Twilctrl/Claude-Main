#!/usr/bin/env python3
"""scope-fence: keep Claude's edits inside the part of the repo you meant.

Two independent limits, set per project:

  * fence   - path globs. An edit to a file outside them is denied (or, in
              ask mode, put to you) with a reason telling Claude why.
  * budget  - how many distinct files Claude may change in one session.
              Past it, each new file needs your say-so.

Roles in one file:

  hook pre-tool     PreToolUse on Edit/Write/MultiEdit/NotebookEdit: decides
  hook post-tool    PostToolUse on the same tools: counts files toward budget
  everything else   the CLI behind /scope-fence:fence

Settings live in plugin data keyed by project path, so a fence never ends up
in a commit. Fails open: any hook error means the normal permission flow.
"""

import fnmatch
import hashlib
import json
import os
import re
import sys
from pathlib import Path

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


# --- locations -------------------------------------------------------------


def claude_home():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def project_dir():
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def data_dir():
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    path = Path(base) if base else claude_home() / "plugin-data" / "scope-fence"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path():
    key = hashlib.sha1(str(project_dir()).encode()).hexdigest()[:12]
    return data_dir() / "projects" / (key + ".json")


def session_path(session):
    return data_dir() / "sessions" / (re.sub(r"[^\w-]", "_", session) + ".json")


# --- config ----------------------------------------------------------------


def blank():
    return {"project": str(project_dir()), "enabled": True, "paths": [],
            "budget": None, "mode": "deny"}


def load():
    cfg = blank()
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    if isinstance(data.get("enabled"), bool):
        cfg["enabled"] = data["enabled"]
    if isinstance(data.get("paths"), list):
        cfg["paths"] = [p for p in data["paths"] if isinstance(p, str)]
    if isinstance(data.get("budget"), int) and data["budget"] > 0:
        cfg["budget"] = data["budget"]
    if data.get("mode") in ("deny", "ask"):
        cfg["mode"] = data["mode"]
    return cfg


def save(cfg):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def touched(session):
    try:
        data = json.loads(session_path(session).read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except (OSError, ValueError):
        return set()


# --- matching --------------------------------------------------------------


def relative(file_path, cwd):
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(cwd or project_dir()) / p
    p = p.resolve()
    try:
        return p.relative_to(project_dir()).as_posix(), True
    except ValueError:
        return p.as_posix(), False


def inside(rel, patterns):
    for pattern in patterns:
        pattern = pattern.strip()
        if fnmatch.fnmatch(rel, pattern) or rel.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


# --- hooks -----------------------------------------------------------------


def target_of(payload):
    tool_input = payload.get("tool_input") or {}
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    return target if isinstance(target, str) else None


def decide(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))


def hook_pre_tool(payload):
    cfg = load()
    target = target_of(payload)
    if not cfg["enabled"] or target is None:
        return
    rel, in_project = relative(target, payload.get("cwd"))

    if cfg["paths"] and not (in_project and inside(rel, cfg["paths"])):
        decide(cfg["mode"],
               "scope-fence: " + rel + " is outside this task's fence ("
               + ", ".join(cfg["paths"]) + "). Don't work around this with "
               "other tools; stop and ask the user whether the change belongs "
               "in scope, and why it's needed.")
        return

    budget = cfg["budget"]
    session = payload.get("session_id")
    if budget and isinstance(session, str):
        done = touched(session)
        if rel not in done and len(done) >= budget:
            decide("ask",
                   "scope-fence: this would be file " + str(len(done) + 1)
                   + " changed this session; the budget is " + str(budget)
                   + ". Already changed: " + ", ".join(sorted(done)) + ".")


def hook_post_tool(payload):
    target = target_of(payload)
    session = payload.get("session_id")
    if target is None or not isinstance(session, str):
        return
    rel, _ = relative(target, payload.get("cwd"))
    done = touched(session)
    if rel in done:
        return
    done.add(rel)
    path = session_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(done)), encoding="utf-8")


# --- cli -------------------------------------------------------------------


def die(message):
    print("scope-fence: " + message, file=sys.stderr)
    sys.exit(1)


def describe(cfg):
    print("project: " + cfg["project"])
    print("state:   " + ("on" if cfg["enabled"] else "off"))
    if cfg["paths"]:
        print("fence:   " + ", ".join(cfg["paths"]) + "   (outside -> " + cfg["mode"] + ")")
    else:
        print("fence:   none - every path is in bounds")
    print("budget:  " + (str(cfg["budget"]) + " files per session" if cfg["budget"] else "none"))


def cmd_status(rest):
    describe(load())


def cmd_set(rest):
    if not rest:
        die("usage: set <glob> [glob...]")
    cfg = load()
    cfg["paths"], cfg["enabled"] = rest, True
    save(cfg)
    describe(cfg)


def cmd_add(rest):
    if not rest:
        die("usage: add <glob> [glob...]")
    cfg = load()
    cfg["paths"] += [p for p in rest if p not in cfg["paths"]]
    save(cfg)
    describe(cfg)


def cmd_drop(rest):
    if not rest:
        die("usage: drop <glob> [glob...]")
    cfg = load()
    missing = [p for p in rest if p not in cfg["paths"]]
    if missing:
        die("not in the fence: " + ", ".join(missing))
    cfg["paths"] = [p for p in cfg["paths"] if p not in rest]
    save(cfg)
    describe(cfg)


def cmd_budget(rest):
    cfg = load()
    value = rest[0] if rest else ""
    if value in ("off", "none", "0"):
        cfg["budget"] = None
    elif value.isdigit():
        cfg["budget"] = int(value)
    else:
        die("usage: budget <n|off>")
    save(cfg)
    describe(cfg)


def cmd_mode(rest):
    if not rest or rest[0] not in ("deny", "ask"):
        die("usage: mode deny|ask")
    cfg = load()
    cfg["mode"] = rest[0]
    save(cfg)
    describe(cfg)


def cmd_toggle(on):
    def run(rest):
        cfg = load()
        cfg["enabled"] = on
        save(cfg)
        describe(cfg)
    return run


def cmd_clear(rest):
    try:
        config_path().unlink()
    except FileNotFoundError:
        pass
    print("Fence and budget removed for " + str(project_dir()) + ".")


COMMANDS = {"status": cmd_status, "set": cmd_set, "add": cmd_add,
            "drop": cmd_drop, "budget": cmd_budget, "mode": cmd_mode,
            "on": cmd_toggle(True), "off": cmd_toggle(False), "clear": cmd_clear}

USAGE = """scope-fence - keep edits inside the part of the repo you meant

  status               current fence and budget (default)
  set <glob>...        fence edits to these paths (src/api/**, tests/)
  add | drop <glob>... widen or narrow the fence
  budget <n|off>       ask before changing more than n files in a session
  mode deny|ask        out-of-fence edits are refused (default) or put to you
  on | off             toggle without losing settings
  clear                forget everything for this project
"""


def main(argv):
    if argv[:1] == ["hook"]:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
            if payload.get("tool_name") in EDIT_TOOLS:
                {"pre-tool": hook_pre_tool, "post-tool": hook_post_tool}[argv[1]](payload)
        except Exception:
            pass
        return 0
    if argv[:1] and argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    name = argv[0] if argv else "status"
    handler = COMMANDS.get(name)
    if handler is None:
        print(USAGE)
        die("unknown command: " + name)
    return handler(argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
