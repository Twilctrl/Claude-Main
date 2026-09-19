# prompt-prefix

Choose a phrase, and it goes at the start of every prompt you send.

Useful for standing instructions you'd otherwise retype: *"Answer in British
English."*, *"Explain before you edit."*, *"Cite file paths."*

## Install

```
/plugin marketplace add twilctrl/claude-main
/plugin install prompt-prefix@claude-main
```

## Use

```
/prompt-prefix:prefix set Explain before you edit.
```

That's it — the phrase is now attached to each prompt until you turn it off.

| Command | What it does |
| --- | --- |
| `/prompt-prefix:prefix` | Show the active phrase and where it's stored |
| `/prompt-prefix:prefix set <phrase>` | Use this phrase from now on |
| `/prompt-prefix:prefix off` / `on` | Toggle without losing the phrase |
| `/prompt-prefix:prefix clear` | Drop the active phrase |
| `/prompt-prefix:prefix save <name> [phrase]` | Remember a phrase (defaults to the active one) |
| `/prompt-prefix:prefix use <name>` | Switch to a saved phrase |
| `/prompt-prefix:prefix list` | List saved phrases (`*` marks the active one) |
| `/prompt-prefix:prefix remove <name>` | Forget a saved phrase |

Add `--scope project` to any writing command to store the setting in the
current repo instead of your user config.

## Where settings live

- User: `~/.claude/prompt-prefix.json` (default; follows you across projects)
- Project: `<project>/.claude/prompt-prefix.json`

If a project file exists it shadows the user one completely, so a repo can
carry its own phrase. `status` always names the file actually in effect.

## How it works

A `UserPromptSubmit` hook runs `scripts/prompt_prefix.py hook` on each prompt
and returns the phrase as `additionalContext`, which Claude sees ahead of what
you typed. Two consequences worth knowing:

- Your typed message is not rewritten — the transcript still shows exactly
  what you sent, with the phrase attached alongside it.
- Prompts starting with `/` are skipped, so slash commands (including this
  plugin's own) run without the phrase attached.

The hook fails open. A missing, unreadable, or malformed config file means no
prefix that turn, never a blocked prompt.

## Requirements

`python3` on `PATH` (standard library only).
