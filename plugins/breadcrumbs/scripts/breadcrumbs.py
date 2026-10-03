#!/usr/bin/env python3
"""breadcrumbs: notes pinned to paths, and notes pinned to dates.

  * crumbs    - a note attached to a path glob. The first time in a session
                Claude reads or edits a matching file, the note is handed to
                it alongside the tool result ("auth.py: tokens are rotated by
                cron, never cache them").
  * capsules  - a note with an opening date. At the first session start on or
                after that date, the note is shown once, then stays sealed.

Roles in one file:

  hook post-tool    PostToolUse on Read/Edit/Write/MultiEdit/NotebookEdit
  hook session      SessionStart: opens due capsules, prunes old state
  everything else   the CLI behind /breadcrumbs:crumb

Fails open: any error in hook mode means no note, never a blocked tool.
"""

import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

CONFIG_NAME = "breadcrumbs.json"
SEEN_TTL_DAYS = 7


# --- locations -------------------------------------------------------------


def claude_home():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def project_dir():
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def user_config():
    return claude_home() / CONFIG_NAME


def project_config():
    return project_dir() / ".claude" / CONFIG_NAME


def data_dir():
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    path = Path(base) if base else claude_home() / "plugin-data" / "breadcrumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_key():
    return hashlib.sha1(str(project_dir().resolve()).encode()).hexdigest()[:12]


# --- config ----------------------------------------------------------------


def blank():
    return {"crumbs": [], "capsules": []}


def load(path):
    cfg = blank()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    for key, needed in (("crumbs", ("id", "glob", "note")),
                        ("capsules", ("id", "opens", "note"))):
        items = data.get(key)
        if isinstance(items, list):
            cfg[key] = [i for i in items if isinstance(i, dict)
                        and all(isinstance(i.get(f), str) for f in needed)]
    return cfg


def save(path, cfg):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def both():
    """Project and user crumbs both apply; nothing shadows anything."""
    return [("project", project_config()), ("user", user_config())]


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# --- matching --------------------------------------------------------------


def relative(file_path, cwd):
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(cwd or project_dir()) / p
    try:
        return p.resolve().relative_to(project_dir().resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def matches(rel, pattern):
    pattern = pattern.strip()
    if fnmatch.fnmatch(rel, pattern):
        return True
    # A bare directory covers everything below it.
    return rel.startswith(pattern.rstrip("/") + "/")


# --- hooks -----------------------------------------------------------------


def emit(event, context):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": context,
    }}))


def hook_post_tool(payload):
    tool_input = payload.get("tool_input") or {}
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    session = payload.get("session_id")
    if not isinstance(target, str) or not isinstance(session, str):
        return
    rel = relative(target, payload.get("cwd"))

    seen_path = data_dir() / "seen" / (re.sub(r"[^\w-]", "_", session) + ".json")
    seen = set(read_json(seen_path, []))

    fresh = []
    for scope, path in both():
        for crumb in load(path)["crumbs"]:
            key = scope + ":" + crumb["id"]
            if key not in seen and matches(rel, crumb["glob"]):
                fresh.append(crumb)
                seen.add(key)
    if not fresh:
        return

    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(sorted(seen)), encoding="utf-8")

    lines = ["Breadcrumb notes left for " + rel + " (keep these in mind):"]
    lines += ["- [" + c["glob"] + "] " + c["note"] for c in fresh]
    emit("PostToolUse", "\n".join(lines))


def hook_session(payload):
    prune_seen()
    today = dt.date.today().isoformat()
    opened_path = data_dir() / "opened.json"
    opened = set(read_json(opened_path, []))

    due = []
    for scope, path in both():
        for cap in load(path)["capsules"]:
            key = project_key() + ":" + scope + ":" + cap["id"]
            if cap["opens"] <= today and key not in opened:
                due.append(cap)
                opened.add(key)
    if not due:
        return
    opened_path.write_text(json.dumps(sorted(opened)), encoding="utf-8")

    lines = ["Time capsules opened today. Mention each to the user at the "
             "start of your first reply, quoted, with the date it was sealed:"]
    for cap in due:
        lines.append("- sealed " + cap.get("added", "?") + ": " + cap["note"])
    emit("SessionStart", "\n".join(lines))


def prune_seen():
    cutoff = time.time() - SEEN_TTL_DAYS * 86400
    for f in (data_dir() / "seen").glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


# --- cli -------------------------------------------------------------------


def die(message):
    print("breadcrumbs: " + message, file=sys.stderr)
    sys.exit(1)


def next_id(items, letter):
    nums = [int(i["id"][1:]) for i in items
            if re.fullmatch(letter + r"\d+", i["id"])]
    return letter + str(max(nums, default=0) + 1)


def parse_date(text):
    m = re.fullmatch(r"\+(\d+)([dwmy])", text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * {"d": 1, "w": 7, "m": 30, "y": 365}[unit]
        return (dt.date.today() + dt.timedelta(days=days)).isoformat()
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError:
        die("dates look like 2026-12-25, +10d, +3w, +6m or +1y (got " + text + ")")


def cmd_add(args):
    if len(args.rest) < 2:
        die("usage: add <glob> <note>")
    path = args.path()
    cfg = load(path)
    crumb = {"id": next_id(cfg["crumbs"], "c"), "glob": args.rest[0],
             "note": " ".join(args.rest[1:]).strip(),
             "added": dt.date.today().isoformat()}
    cfg["crumbs"].append(crumb)
    save(path, cfg)
    print("Dropped " + crumb["id"] + " on " + crumb["glob"] + " (" + str(path) + ")")


def cmd_capsule(args):
    if len(args.rest) < 2:
        die("usage: capsule <date|+30d> <note>")
    path = args.path()
    cfg = load(path)
    cap = {"id": next_id(cfg["capsules"], "t"), "opens": parse_date(args.rest[0]),
           "note": " ".join(args.rest[1:]).strip(),
           "added": dt.date.today().isoformat()}
    cfg["capsules"].append(cap)
    save(path, cfg)
    print("Sealed " + cap["id"] + ", opens " + cap["opens"] + " (" + str(path) + ")")


def cmd_remove(args):
    if not args.rest:
        die("usage: remove <id>")
    target = args.rest[0]
    path = args.path()
    cfg = load(path)
    for key in ("crumbs", "capsules"):
        kept = [i for i in cfg[key] if i["id"] != target]
        if len(kept) != len(cfg[key]):
            cfg[key] = kept
            save(path, cfg)
            print("Removed " + target + " from " + str(path))
            return
    die("no " + target + " in " + str(path) + " (try --scope user)")


def cmd_list(args):
    today = dt.date.today().isoformat()
    any_shown = False
    for scope, path in both():
        cfg = load(path)
        if not cfg["crumbs"] and not cfg["capsules"]:
            continue
        any_shown = True
        print(scope + "  " + str(path))
        for c in cfg["crumbs"]:
            print("  " + c["id"].ljust(4) + c["glob"] + "  -> " + c["note"])
        for t in cfg["capsules"]:
            state = "open" if t["opens"] <= today else "sealed until " + t["opens"]
            note = t["note"] if t["opens"] <= today else "(hidden)"
            print("  " + t["id"].ljust(4) + "[" + state + "]  " + note)
    if not any_shown:
        print("No breadcrumbs or capsules yet. Try: add src/db/** Migrations run in CI only.")


def cmd_check(args):
    if not args.rest:
        die("usage: check <path>")
    rel = relative(args.rest[0], os.getcwd())
    hits = [(s, c) for s, p in both() for c in load(p)["crumbs"] if matches(rel, c["glob"])]
    if not hits:
        print("No breadcrumbs match " + rel)
    for scope, c in hits:
        print(scope + " " + c["id"] + " [" + c["glob"] + "] " + c["note"])


COMMANDS = {"add": cmd_add, "capsule": cmd_capsule, "remove": cmd_remove,
            "list": cmd_list, "check": cmd_check}

USAGE = """breadcrumbs - notes that surface when a file is touched, or a date arrives

  list                       everything, project and user (default)
  add <glob> <note>          note for files matching glob (src/auth/**, *.sql)
  capsule <date|+Nd> <note>  note that opens at the first session on/after date
  check <path>               which notes would fire for this path
  remove <id>                delete a crumb (c1...) or capsule (t1...)

  --scope project|user       where to write (default: project, so teammates
                             who commit .claude/breadcrumbs.json share it)
"""


class Args:
    def __init__(self, scope, rest):
        self.scope = scope
        self.rest = rest

    def path(self):
        return user_config() if self.scope == "user" else project_config()


def main(argv):
    if argv[:1] == ["hook"]:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
            {"post-tool": hook_post_tool, "session": hook_session}[argv[1]](payload)
        except Exception:
            pass
        return 0

    scope, rest, i = "project", [], 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--scope"):
            if "=" in arg:
                scope = arg.split("=", 1)[1]
            else:
                i += 1
                scope = argv[i] if i < len(argv) else ""
            if scope not in ("user", "project"):
                die("--scope takes user or project")
        elif arg in ("-h", "--help", "help"):
            print(USAGE)
            return 0
        else:
            rest.append(arg)
        i += 1

    name = rest[0] if rest else "list"
    handler = COMMANDS.get(name)
    if handler is None:
        print(USAGE)
        die("unknown command: " + name)
    return handler(Args(scope, rest[1:])) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
