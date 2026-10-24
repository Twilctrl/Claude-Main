"""Built-in tools: workspace files, blackboard, task board, messaging, verdicts.

Shell and web fetch live in their own modules (:mod:`hive.tools.shell`,
:mod:`hive.tools.web`) because they're off unless an agent lists them.
"""

from __future__ import annotations

from ..context import clip
from ..runtime.board import BoardError
from ..runtime.files import is_binary
from . import ToolContext, ToolError, ToolResult, tool

# --- files ----------------------------------------------------------------------------


@tool(group="files")
async def list_files(ctx: ToolContext, path: str = ".") -> str:
    """List the files in the project folder (or a subfolder), with sizes.

    Args:
        path: folder to list, relative to the project folder
    """
    files, truncated = ctx.run.workspace.list(path, recursive=True)
    if not files:
        return "(no files yet)"
    lines = [f"{f.path} ({f.size} bytes)" for f in files]
    if truncated:
        lines.append("(list truncated)")
    return "\n".join(lines)


@tool(group="files")
async def read_file(ctx: ToolContext, path: str, start_line: int = 1, max_lines: int = 200) -> str:
    """Read a text file from the project folder, a page at a time.

    Args:
        path: file to read, relative to the project folder
        start_line: first line to return (1 = the start)
        max_lines: how many lines to return
    """
    data = await ctx.run.files.read(ctx.agent.name, path)
    if is_binary(data):
        return f"{path} is a binary file ({len(data)} bytes)"
    lines = data.decode("utf-8", "replace").splitlines()
    start = max(1, start_line)
    count = max(1, min(max_lines, 1000))
    chunk = lines[start - 1 : start - 1 + count]
    end = start - 1 + len(chunk)
    header = f"--- {path} (lines {start}-{end} of {len(lines)}) ---" if len(lines) > count or start > 1 else f"--- {path} ---"
    more = f"\n--- {len(lines) - end} more lines: read_file with start_line={end + 1} ---" if end < len(lines) else ""
    return header + "\n" + "\n".join(chunk) + more


@tool(group="files")
async def write_file(ctx: ToolContext, path: str, content: str) -> ToolResult:
    """Create a file, or replace a whole file, in the project folder.

    Args:
        path: file to write, relative to the project folder, e.g. src/app.py
        content: the complete new contents of the file
    """
    info = await ctx.run.files.write(ctx.agent.name, path, content)
    if info.get("unchanged"):
        return ToolResult(f"{info['path']} already has exactly this content; nothing changed.")
    verb = "Created" if info.get("created") else "Updated"
    return ToolResult(f"{verb} {info['path']} ({info['bytes']} bytes).", data=info, progress=True)


@tool(group="files")
async def edit_file(ctx: ToolContext, path: str, find: str, replace: str, replace_all: bool = False) -> ToolResult:
    """Change part of a file: replace the exact text `find` with `replace`.

    Args:
        path: file to change, relative to the project folder
        find: text that's in the file now, copied exactly (include enough to be unique)
        replace: the text to put in its place
        replace_all: replace every occurrence instead of exactly one
    """
    info = await ctx.run.files.edit(ctx.agent.name, path, find, replace, replace_all)
    return ToolResult(f"Edited {info['path']} ({info['replacements']} replacement(s)).", data=info, progress=True)


@tool(group="files", risky=True)
async def delete_file(ctx: ToolContext, path: str) -> ToolResult:
    """Delete a file from the project folder. Needs the user's approval.

    Args:
        path: file to delete, relative to the project folder
    """
    info = await ctx.run.files.delete(ctx.agent.name, path)
    return ToolResult(f"Deleted {info['path']}.", data=info, progress=True)


# --- blackboard ----------------------------------------------------------------------------


@tool(group="board")
async def post_note(ctx: ToolContext, title: str, content: str = "") -> ToolResult:
    """Pin a note to the team's shared board: a finding, a fact, a reminder.

    Args:
        title: a short headline
        content: the details
    """
    e = await ctx.run.board.add("note", title, content, ctx.agent.name)
    return ToolResult(f"Posted note {e.id}.", progress=True)


@tool(group="board")
async def record_decision(ctx: ToolContext, title: str, content: str = "") -> ToolResult:
    """Record a decision the team should stick to (a design choice, a convention).

    Args:
        title: the decision, in a few words
        content: why, and any details
    """
    e = await ctx.run.board.add("decision", title, content, ctx.agent.name)
    return ToolResult(f"Recorded decision {e.id}.", progress=True)


@tool(group="board")
async def read_board(ctx: ToolContext, kind: str = "", query: str = "") -> str:
    """Read the team's shared board: notes, decisions, artifacts.

    Args:
        kind: only this kind: note, decision, artifact or deliverable (empty: all)
        query: only entries containing this text
    """
    entries = ctx.run.board.find(kind, query)
    if not entries:
        return "(the board is empty)" if not (kind or query) else "(nothing matches)"
    return "\n\n".join(f"[{e.kind} {e.id} by {e.author}] {e.title}\n{clip(e.content, 800)}".strip() for e in entries)


# --- tasks -------------------------------------------------------------------------------------


@tool(group="tasks")
async def list_tasks(ctx: ToolContext, status: str = "") -> str:
    """Show the task board.

    Args:
        status: only tasks with this status: todo, in_progress, done or failed (empty: all)
    """
    tasks = [t for t in ctx.run.tasks.tasks.values() if not status or t.status == status]
    if not tasks:
        return "(no tasks)"
    out = []
    for t in tasks:
        line = t.line()
        if t.result:
            line += f"\n    result: {clip(t.result, 300)}"
        out.append(line)
    return "\n".join(out)


@tool(group="tasks")
async def create_task(
    ctx: ToolContext, title: str, description: str = "", depends_on: list[int] = [], assign_to: str = ""  # noqa: B006
) -> ToolResult:
    """Add a task to the board.

    Args:
        title: short imperative title, e.g. "Write parser.py"
        description: exactly what to do and what "done" means
        depends_on: ids of tasks that must be done first
        assign_to: teammate name to give it to (empty: leave it for someone to claim)
    """
    run = ctx.run
    owner = assign_to.strip() or None
    if owner and owner not in run.agents:
        raise ToolError(f"no teammate named {owner!r} (team: {', '.join(run.agents)})")
    t = await run.tasks.create(title, description, created_by=ctx.agent.name, depends_on=depends_on, assign_to=owner)
    who = f", assigned to {owner}" if owner else ""
    return ToolResult(f"Created task #{t.id}{who}.", data={"task": t.id}, progress=True)


@tool(group="tasks")
async def assign_task(ctx: ToolContext, task_id: int, agent: str) -> ToolResult:
    """Give a task to a teammate.

    Args:
        task_id: the task's number
        agent: the teammate's name
    """
    if agent not in ctx.run.agents:
        raise ToolError(f"no teammate named {agent!r} (team: {', '.join(ctx.run.agents)})")
    await ctx.run.tasks.assign(task_id, agent, by=ctx.agent.name)
    return ToolResult(f"Task #{task_id} assigned to {agent}.", progress=True)


@tool(group="tasks")
async def claim_task(ctx: ToolContext, task_id: int) -> ToolResult:
    """Take a ready task from the board and start on it.

    Args:
        task_id: the task's number
    """
    t = await ctx.run.tasks.claim(task_id, ctx.agent.name)
    ctx.agent.current_task = t.id
    return ToolResult(f"You're on task #{t.id}: {t.title}\n{t.description}", progress=True)


@tool(group="tasks")
async def complete_task(ctx: ToolContext, task_id: int, result: str) -> ToolResult:
    """Mark a task done, with a short summary of the result.

    Args:
        task_id: the task's number
        result: what you did and where it is (files, notes)
    """
    await ctx.run.tasks.complete(task_id, ctx.agent.name, result)
    return ToolResult(f"Task #{task_id} is done.", progress=True)


@tool(group="tasks")
async def fail_task(ctx: ToolContext, task_id: int, reason: str) -> ToolResult:
    """Give up on a task you can't do, and say why.

    Args:
        task_id: the task's number
        reason: what went wrong or what's missing
    """
    await ctx.run.tasks.fail(task_id, ctx.agent.name, reason)
    return ToolResult(f"Task #{task_id} marked failed.", progress=True)


@tool(group="tasks")
async def handoff_task(ctx: ToolContext, task_id: int, to: str, note: str = "") -> ToolResult:
    """Pass a task to a teammate who's better placed to do it.

    Args:
        task_id: the task's number
        to: the teammate's name
        note: what you've done so far and what's left
    """
    if to not in ctx.run.agents:
        raise ToolError(f"no teammate named {to!r} (team: {', '.join(ctx.run.agents)})")
    await ctx.run.tasks.handoff(task_id, ctx.agent.name, to, note)
    if ctx.agent.current_task == task_id:
        ctx.agent.current_task = None
    return ToolResult(f"Task #{task_id} handed to {to}.", progress=True)


# --- messages ------------------------------------------------------------------------------------


@tool(group="messages")
async def send_message(ctx: ToolContext, to: str, text: str) -> str:
    """Send a direct message to one teammate (or "user" to ask the person running Hive).

    Args:
        to: teammate name, or "user"
        text: the message
    """
    try:
        await ctx.run.send(ctx.agent.name, to, text)
    except BoardError as e:
        raise ToolError(str(e)) from None
    return f"Sent to {to}."


@tool(group="messages")
async def broadcast(ctx: ToolContext, text: str) -> str:
    """Post a message to the whole team's channel. Teammates see it on their next turn.

    Args:
        text: the message
    """
    await ctx.run.send(ctx.agent.name, "#channel", text)
    return "Posted to the team channel."


# --- verdicts and finishing (the mode decides what they mean) -------------------------------------------


@tool(group="review")
async def approve(ctx: ToolContext, notes: str = "") -> ToolResult:
    """Approve the work you were asked to check. This ends your turn.

    Args:
        notes: what you checked and why it's good
    """
    await ctx.run.mode_event("verdict", agent=ctx.agent.name, approved=True, notes=notes)
    return ToolResult(f"APPROVED. {notes}".strip(), progress=True, end_turn=True)


@tool(group="review")
async def request_changes(ctx: ToolContext, notes: str) -> ToolResult:
    """Send the work back with specific fixes. This ends your turn.

    Args:
        notes: a numbered list of what must change
    """
    await ctx.run.mode_event("verdict", agent=ctx.agent.name, approved=False, notes=notes)
    return ToolResult(f"CHANGES REQUESTED.\n{notes}", progress=True, end_turn=True)


@tool(group="review")
async def finish_run(ctx: ToolContext, summary: str) -> ToolResult:
    """Declare the goal met and end the run. This ends your turn.

    Args:
        summary: what was delivered and which files hold it
    """
    await ctx.run.mode_event("finish", agent=ctx.agent.name, summary=summary)
    return ToolResult(f"Finishing the run: {summary}", progress=True, end_turn=True)
