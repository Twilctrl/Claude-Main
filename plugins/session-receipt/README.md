# session-receipt

Ask for the bill. Get an itemised receipt of what your session actually did.

```
           CLAUDE CODE
       ~ session receipt ~
      claude-main · 2026-10-03
          ticket 4f1c09ab
----------------------------------
  14:02 -> 14:48           46 min
  prompts                       7
----------------------------------
  Read                     x 12
  Edit                     x  9
  Bash                     x  6
  Grep                     x  4
  Write                    x  2
----------------------------------
  files read                    8
  files changed                 4
  most handled
    src/api/pager.py         (9)
  favourite command
    npm test                 (5)
  longest pause            11 min
----------------------------------
  TOOL CALLS                   33
----------------------------------

      thank you, come again
```

## Install

```
/plugin marketplace add twilctrl/claude-main
/plugin install session-receipt@claude-main
```

## Use

| Command | What it does |
| --- | --- |
| `/session-receipt:receipt` | Receipt for the current session |
| `/session-receipt:receipt 2` | The session before that in this project (and so on) |
| `/session-receipt:receipt list` | Recent sessions here, with length and call count |

## How it works

Three hooks append one JSON line per event to a per-session log:

- `SessionStart` — marks the start (and deletes logs older than 30 days)
- `UserPromptSubmit` — counts a prompt
- `PostToolUse` (every tool) — the tool name, plus the project-relative file
  path or the first two words of a shell command

Nothing else is recorded: no prompt text, no file contents, no command
arguments past the second word. Logs live in `${CLAUDE_PLUGIN_DATA}` (or
`~/.claude/plugin-data/session-receipt/logs/`).

"Current session" is the most recently active log for this project, so two
simultaneous sessions in the same repo will see whichever was used last.

Hooks fail open: a logging error never affects the session.

## Requirements

`python3` on `PATH` (standard library only).
