---
description: Print an itemised receipt for this session (or an earlier one)
argument-hint: "[n|list]"
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/session_receipt.py" $ARGUMENTS`

Show the output above to the user exactly as printed, inside its code block. Add nothing else unless the output is an error.
