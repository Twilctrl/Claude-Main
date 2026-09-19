#!/usr/bin/env python3
"""prompt-prefix: keep a chosen phrase at the start of every prompt.

Two roles in one file:

  * ``hook``  - run from the UserPromptSubmit hook. Reads the hook payload on
                stdin and prints the active phrase as additional context so it
                lands ahead of what you typed.
  * everything else - the small CLI the /prompt-prefix:prefix command drives.

Fails open: if anything goes wrong in hook mode the prompt is left untouched.
"""

import json
import os
import sys
from pathlib import Path

CONFIG_NAME = "prompt-prefix.json"


def user_config():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")
    return Path(base) / CONFIG_NAME


def project_config():
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project:
        return None
    return Path(project) / ".claude" / CONFIG_NAME


def blank():
    return {"enabled": True, "prefix": None, "saved": {}}


def load(path):
    """Read one config file, tolerating anything unexpected inside it."""
    cfg = blank()
    if path is None or not path.exists():
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    if isinstance(data.get("enabled"), bool):
        cfg["enabled"] = data["enabled"]
    if isinstance(data.get("prefix"), str):
        cfg["prefix"] = data["prefix"]
    saved = data.get("saved")
    if isinstance(saved, dict):
        cfg["saved"] = {k: v for k, v in saved.items() if isinstance(v, str)}
    return cfg


def save(path, cfg):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def active_path():
    """A project config shadows the user one entirely, when it exists."""
    project = project_config()
    if project is not None and project.exists():
        return project
    return user_config()


def scope_path(scope):
    if scope == "project":
        path = project_config()
        if path is None:
            die("no project directory: run this from inside a project, or use --scope user")
        return path
    return user_config()


def die(message):
    print("prompt-prefix: " + message, file=sys.stderr)
    sys.exit(1)


def show(text):
    return repr(text) if text is None else '"' + text + '"'


# --- hook ------------------------------------------------------------------


def run_hook():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}

    prompt = payload.get("prompt")
    if isinstance(prompt, str) and prompt.lstrip().startswith("/"):
        # Don't prefix slash commands - especially not our own.
        return

    cfg = load(active_path())
    if not cfg["enabled"]:
        return
    prefix = (cfg["prefix"] or "").strip()
    if not prefix:
        return

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": prefix,
        }
    }))


# --- cli -------------------------------------------------------------------


def cmd_status(args):
    path = active_path()
    cfg = load(path)
    other = user_config() if path != user_config() else project_config()
    print("config:  " + str(path) + ("" if path.exists() else "  (not created yet)"))
    if path != user_config():
        print("         shadows " + str(user_config()))
    print("state:   " + ("on" if cfg["enabled"] else "off"))
    print("phrase:  " + show(cfg["prefix"]))
    if cfg["enabled"] and (cfg["prefix"] or "").strip():
        print("\nEvery prompt now starts with: " + cfg["prefix"].strip())
    else:
        print("\nNothing is being prepended.")
    if cfg["saved"]:
        print("\nsaved phrases:")
        for name in sorted(cfg["saved"]):
            print("  " + name + ": " + cfg["saved"][name])


def cmd_set(args):
    text = " ".join(args.rest).strip()
    if not text:
        die("nothing to set: /prompt-prefix:prefix set <phrase>")
    path = scope_path(args.scope)
    cfg = load(path)
    cfg["prefix"] = text
    cfg["enabled"] = True
    save(path, cfg)
    print("Prefix set in " + str(path) + ":")
    print("  " + text)
    warn_shadowed(path)


def cmd_clear(args):
    path = scope_path(args.scope)
    cfg = load(path)
    cfg["prefix"] = None
    save(path, cfg)
    print("Prefix cleared in " + str(path) + ". Saved phrases are untouched.")


def cmd_on(args):
    path = scope_path(args.scope)
    cfg = load(path)
    cfg["enabled"] = True
    save(path, cfg)
    if (cfg["prefix"] or "").strip():
        print("On. Prompts start with: " + cfg["prefix"].strip())
    else:
        print("On, but no phrase is set yet - use `set <phrase>`.")
    warn_shadowed(path)


def cmd_off(args):
    path = scope_path(args.scope)
    cfg = load(path)
    cfg["enabled"] = False
    save(path, cfg)
    print("Off. The phrase is kept and can be restored with `on`.")


def cmd_save(args):
    name = args.rest[0] if args.rest else ""
    if not name:
        die("name the phrase: /prompt-prefix:prefix save <name> [phrase]")
    path = scope_path(args.scope)
    cfg = load(path)
    text = " ".join(args.rest[1:]).strip() or (cfg["prefix"] or "").strip()
    if not text:
        die("no phrase to save: pass one, or `set` a phrase first")
    cfg["saved"][name] = text
    save(path, cfg)
    print("Saved as " + name + ": " + text)


def cmd_use(args):
    name = args.rest[0] if args.rest else ""
    if not name:
        die("which one? /prompt-prefix:prefix use <name>")
    path = scope_path(args.scope)
    cfg = load(path)
    if name not in cfg["saved"]:
        known = ", ".join(sorted(cfg["saved"])) or "(none saved)"
        die("no saved phrase called " + name + ". Known: " + known)
    cfg["prefix"] = cfg["saved"][name]
    cfg["enabled"] = True
    save(path, cfg)
    print("Now using " + name + ": " + cfg["prefix"])
    warn_shadowed(path)


def cmd_remove(args):
    name = args.rest[0] if args.rest else ""
    if not name:
        die("which one? /prompt-prefix:prefix remove <name>")
    path = scope_path(args.scope)
    cfg = load(path)
    if cfg["saved"].pop(name, None) is None:
        die("no saved phrase called " + name)
    save(path, cfg)
    print("Removed " + name + ". The active phrase is unchanged.")


def cmd_list(args):
    cfg = load(active_path())
    if not cfg["saved"]:
        print("No saved phrases yet. Save one with `save <name> <phrase>`.")
        return
    current = (cfg["prefix"] or "").strip()
    for name in sorted(cfg["saved"]):
        text = cfg["saved"][name]
        marker = "* " if text.strip() == current and cfg["enabled"] else "  "
        print(marker + name + ": " + text)


def warn_shadowed(path):
    project = project_config()
    if path == user_config() and project is not None and project.exists():
        print("\nNote: " + str(project) + " exists and takes precedence here.")


COMMANDS = {
    "status": cmd_status,
    "set": cmd_set,
    "clear": cmd_clear,
    "on": cmd_on,
    "off": cmd_off,
    "save": cmd_save,
    "use": cmd_use,
    "remove": cmd_remove,
    "list": cmd_list,
}

USAGE = """prompt-prefix - a phrase at the start of every prompt

  status                 what is active right now (default)
  set <phrase>           use this phrase from now on
  clear                  stop prepending, keep saved phrases
  on | off               toggle without losing the phrase
  save <name> [phrase]   remember a phrase (defaults to the active one)
  use <name>             switch to a saved phrase
  remove <name>          forget a saved phrase
  list                   show saved phrases

  --scope user|project   where to write (default: user)
"""


class Args:
    def __init__(self, scope, rest):
        self.scope = scope
        self.rest = rest


def main(argv):
    if argv and argv[0] == "hook":
        try:
            run_hook()
        except Exception:  # never break a prompt over this
            pass
        return 0

    scope = "user"
    rest = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--scope":
            i += 1
            if i >= len(argv) or argv[i] not in ("user", "project"):
                die("--scope takes user or project")
            scope = argv[i]
        elif arg.startswith("--scope="):
            scope = arg.split("=", 1)[1]
            if scope not in ("user", "project"):
                die("--scope takes user or project")
        elif arg in ("-h", "--help", "help"):
            print(USAGE)
            return 0
        else:
            rest.append(arg)
        i += 1

    name = rest[0] if rest else "status"
    handler = COMMANDS.get(name)
    if handler is None:
        print(USAGE)
        die("unknown command: " + name)
    return handler(Args(scope, rest[1:])) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
