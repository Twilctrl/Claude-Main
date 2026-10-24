"""Export a run as a Markdown transcript, built from its event log."""

from __future__ import annotations

import json
import time
from typing import Any

from .context import clip
from .runtime.files import changes_since
from .store import Store


def _when(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _quote(text: str) -> str:
    return "\n".join("> " + line for line in text.strip().splitlines()) or "> "


def _fence(text: str, lang: str = "") -> str:
    ticks = "````" if "```" in text else "```"
    return f"{ticks}{lang}\n{text.rstrip()}\n{ticks}"


async def transcript(store: Store, run_id: str, *, include_diffs: bool = True) -> str:
    run = await store.get_run(run_id)
    if not run:
        raise ValueError(f"no run {run_id!r}")
    project = await store.get_project(run["project_id"]) or {}
    events = await store.events(run["id"])
    stats = run.get("stats") or {}
    started = time.strftime("%Y-%m-%d %H:%M", time.localtime(run["started_at"]))

    out: list[str] = [f"# Hive run: {run['goal']}", ""]
    out += [
        "| | |",
        "| --- | --- |",
        f"| Run | `{run['id']}` |",
        f"| Mode | {run['mode']} |",
        f"| Status | **{run['status']}** |",
        f"| Started | {started} |",
        f"| Project | {project.get('name', '?')} (`{project.get('workspace', '?')}`) |",
    ]
    if stats:
        out.append(f"| Usage | {stats.get('turns', '?')} turns, {stats.get('tokens', '?')} tokens, {stats.get('seconds', '?')} s |")
    out += ["", "## Team", "", "| Agent | Role | Backend | Model |", "| --- | --- | --- | --- |"]
    for a in run["config"].get("team", []):
        out.append(f"| {a['name']} | {a['role']} | {a['backend']} | {a.get('model') or '(default)'} |")

    if run.get("summary"):
        out += ["", "## Deliverable", "", run["summary"].strip()]
    finished = next((e for e in reversed(events) if e["kind"] == "run.finished"), None)
    if finished and finished["payload"].get("reason"):
        out += ["", f"_Ended: {finished['payload']['reason']}_"]

    tasks = await store.tasks(run["id"])
    if tasks:
        out += ["", "## Tasks", "", "| # | Task | Owner | Status | Result |", "| --- | --- | --- | --- | --- |"]
        for t in tasks:
            result = clip(" ".join((t.get("result") or "").split()), 200).replace("|", "\\|")
            out.append(f"| {t['id']} | {t['title'].replace('|', '/')} | {t.get('owner') or ''} | {t['status']} | {result} |")

    board = [e for e in await store.board(run["id"]) if e["kind"] in ("decision", "note")]
    if board:
        out += ["", "## Board", ""]
        for e in board:
            out.append(f"- **{e['kind']}** by {e['author']}: **{e['title']}** {clip(' '.join(e['content'].split()), 400)}")

    out += ["", "## Timeline", ""]
    for e in events:
        out += _event_lines(e)

    if include_diffs:
        changed, diff = await changes_since(store, run["project_id"], run["id"], 0, max_chars=60_000)
        if changed:
            out += ["", "## Changes", "", "Files: " + ", ".join(f"`{c}`" for c in changed), "", _fence(diff, "diff")]
    return "\n".join(out).rstrip() + "\n"


def _event_lines(e: dict[str, Any]) -> list[str]:
    p, a, k = e["payload"], e["agent"], e["kind"]
    rb = " _(rolled back)_" if e.get("rolled_back") else ""
    t = _when(e["ts"])
    if k == "agent.turn":
        return ["", f"### {t} · {a}, turn {p['turn']}{rb}", "", _quote(clip(p["trigger"], 1500))]
    if k == "agent.reply":
        return ["", f"**{a}:**{rb}", "", clip(p["text"].strip(), 4000)]
    if k == "tool.call":
        args = json.dumps(p["args"], ensure_ascii=False, indent=1)
        return [f"<details><summary>{t} {a} → <code>{p['tool']}</code>{rb}</summary>", "", _fence(clip(args, 3000), "json"), "", "</details>"]
    if k == "tool.result":
        mark = "←" if p.get("ok") else "✗"
        return [f"<details><summary>{t} {a} {mark} {p['tool']} result</summary>", "", _fence(clip(p["output"], 3000)), "", "</details>"]
    if k == "message":
        return [f"- {t} ✉ **{p['sender']} → {p['recipient']}**{rb}: {clip(' '.join(p['content'].split()), 1000)}"]
    if k == "task.updated" and p["change"] in ("created", "done", "failed") or k == "task.updated" and p["change"].startswith("assigned"):
        task = p["task"]
        return [f"- {t} task #{task['id']} **{p['change']}**: {task['title']}{rb}"]
    if k == "file.version":
        what = "deleted" if p["deleted"] else ("created" if p["created"] else "changed")
        return [f"- {t} ✎ `{p['path']}` {what} by {a} (v{p['version']}, +{p['added']} −{p['removed']}){rb}"]
    if k in ("approval.requested", "approval.decided"):
        if k == "approval.requested":
            return [f"- {t} ⚠ {a} asked to run **{p['tool']}**: `{clip(json.dumps(p['args'], ensure_ascii=False), 300)}`"]
        verdict = "approved" if p["approved"] else "rejected"
        return [f"- {t} {p.get('tool', 'request')} {verdict} by {p['by']}{': ' + p['reason'] if p.get('reason') else ''}"]
    if k == "guard.trigger":
        return [f"- {t} 🛡 loop guard ({p['rule']}) on {a}: {p['action']}, {p['detail']}"]
    if k == "mode.verdict":
        return [f"- {t} {'✅ approved' if p['approved'] else '↩ changes requested'} by {a} (round {p['round']})"]
    if k == "mode.stage":
        return [f"- {t} ▶ stage: {a} ({p['kind']}, round {p['round']})"]
    if k == "mode.phase":
        return [f"- {t} ▶ debate phase: {p['phase']}"]
    if k == "checkpoint.restored":
        return [f"- {t} ⏪ rolled back to checkpoint {p['id']} ({p['label']})"]
    if k == "run.attention":
        return [f"- {t} ⚠ {p['message']}"]
    if k in ("backend.down", "backend.up"):
        return [f"- {t} backend {p['backend']} is {k.split('.')[1]}"]
    if k == "agent.updated":
        return [f"- {t} ✏ {a} edited: {', '.join(p['changes'])}"]
    if k == "agent.error":
        return [f"- {t} ❌ {a}: {p['error']}"]
    return []
