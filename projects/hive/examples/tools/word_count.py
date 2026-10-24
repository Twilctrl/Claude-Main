"""An example tool plugin. Copy it into your Hive config folder's tools/ directory
(`hive init` creates it; e.g. ~/.config/hive/tools/), then list `word_count` in an
agent's tools (a role file or a team file).

Plugins run inside Hive with your permissions, so only install ones you trust.
Use the ctx.run.workspace / ctx.run.files helpers for file access so the
workspace sandbox still applies.
"""

from hive.tools import ToolContext, ToolResult, tool


@tool(group="files")
async def word_count(ctx: ToolContext, path: str) -> ToolResult:
    """Count the lines, words and characters in a text file.

    Args:
        path: the file, relative to the project folder
    """
    data = await ctx.run.files.read(ctx.agent.name, path)
    text = data.decode("utf-8", "replace")
    return ToolResult(f"{path}: {len(text.splitlines())} lines, {len(text.split())} words, {len(text)} characters")
