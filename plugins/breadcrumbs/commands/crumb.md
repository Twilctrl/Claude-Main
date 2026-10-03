---
description: Leave notes on file paths, or seal a time capsule for a future date
argument-hint: "[list|add <glob> <note>|capsule <date|+30d> <note>|check <path>|remove <id>] [--scope user]"
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/breadcrumbs.py" $ARGUMENTS`

Report the result above to the user in one short line (for `list`, show the list as printed). Do not run any other command unless they ask for something the output says failed.
