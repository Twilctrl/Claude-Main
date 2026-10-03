---
description: Fence edits to certain paths, or cap files changed per session
argument-hint: "[status|set <glob>...|add <glob>...|drop <glob>...|budget <n|off>|mode deny|ask|on|off|clear]"
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scope_fence.py" $ARGUMENTS`

Report the result above to the user in one or two short lines. Do not run any other command unless they ask for something the output says failed.
