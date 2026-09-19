---
description: Choose the phrase that goes at the start of every prompt
argument-hint: "[status|set <phrase>|on|off|clear|save <name>|use <name>|remove <name>|list]"
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/prompt_prefix.py" $ARGUMENTS`

Report the result above to the user in one short line. Do not run any other command unless they ask for something the output says failed.
