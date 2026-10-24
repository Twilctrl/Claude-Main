"""CLI commands that work with runs: run, runs, show, projects, roles."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from .cli import _EXTRA_COMMANDS, _settings, bold, dim, err, green, out, red, yellow
from .config import ConfigError
from .context import clip
from .runtime.app import Hive
from .runtime.events import Event
from .runtime.run import Run

# --- the live event printer -------------------------------------------------------------


def one_line(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


class Printer:
    """Turns run events into terminal lines."""

    def __init__(self, verbose: bool = False, stream: bool = False):
        self.verbose = verbose
        self.stream = stream
        self.streaming: str | None = None  # agent whose tokens are being printed
        self.t0 = time.time()

    def _end_stream(self) -> None:
        if self.streaming:
            out()
            self.streaming = None

    def line(self, who: str | None, text: str) -> None:
        self._end_stream()
        stamp = dim(time.strftime("%H:%M:%S"))
        tag = bold(f"[{who}]") if who else ""
        out(f"{stamp} {tag} {text}".replace("  ", " "))

    def __call__(self, ev: Event) -> None:
        p, a, k = ev.payload, ev.agent, ev.kind
        if k == "agent.delta":
            if self.stream:
                if self.streaming != a:
                    self._end_stream()
                    out(dim(f"  {a}: "), end="")
                    self.streaming = a
                out(p["text"], end="")
            return
        if k == "run.started":
            team = ", ".join(f"{m['name']}({m['backend']}{'/' + m['model'] if m.get('model') else ''})" for m in p["team"])
            self.line(None, f"▶ {p['mode']} run started · team: {team}")
            self.line(None, dim(f"workspace: {p['workspace']}"))
        elif k == "agent.turn":
            first = p["trigger"].strip().splitlines()[0] if p["trigger"].strip() else ""
            self.line(a, dim(f"turn {p['turn']}: {clip(first, 100)}"))
        elif k == "agent.status" and p["status"] in ("waiting_backend", "waiting_approval", "paused", "error"):
            self.line(a, yellow(f"{p['status'].replace('_', ' ')}{': ' + p['detail'] if p['detail'] else ''}"))
        elif k == "tool.call":
            args = json.dumps(p["args"], ensure_ascii=False)
            self.line(a, f"→ {p['tool']} {dim(one_line(args, 160) if not self.verbose else clip(args, 2000))}")
        elif k == "tool.result":
            res = p["output"].strip()
            mark = green("←") if p["ok"] else red("✗")
            body = res if self.verbose else one_line(" · ".join(res.splitlines()[:2]), 160)
            self.line(a, f"{mark} {dim(body)}")
        elif k == "agent.reply":
            self.line(a, f"says: {one_line(p['text'], 300) if not self.verbose else clip(p['text'].strip(), 4000)}")
        elif k == "agent.protocol":
            self.line(a, yellow(f"format: {p['detail']}"))
        elif k == "agent.context":
            self.line(a, dim(f"context {p['how']}: {p['dropped']} old messages folded away"))
        elif k == "agent.error":
            self.line(a, red(f"error: {p['error']}"))
        elif k == "message":
            self.line(p["sender"], f"✉ to {p['recipient']}: {one_line(p['content'], 300)}")
        elif k == "task.updated":
            t = p["task"]
            owner = f" → {t['owner']}" if t.get("owner") else ""
            self.line("task", f"#{t['id']} {p['change']}{owner}: {clip(t['title'], 80)} [{t['status']}]")
        elif k == "board.entry":
            e = p["entry"]
            self.line(a, f"📌 {e['kind']}: {clip(e['title'], 100)}")
        elif k == "file.version":
            what = "deleted" if p["deleted"] else ("created" if p["created"] else "changed")
            self.line(a, f"✎ {p['path']} {what} (v{p['version']}, +{p['added']} -{p['removed']})")
        elif k == "approval.requested":
            self.line(a, yellow(f"wants approval for {p['tool']}: {one_line(json.dumps(p['args']), 200)}"))
        elif k == "approval.decided":
            verdict = green("approved") if p["approved"] else red("rejected")
            self.line(a, f"{p.get('tool', 'request')} {verdict} by {p['by']}{': ' + p['reason'] if p.get('reason') else ''}")
        elif k == "guard.trigger":
            self.line(a, yellow(f"loop guard ({p['rule']}): {p['action']} · {p['detail']}"))
        elif k in ("model.rerouted",):
            self.line(a, yellow(f"rerouted {p['from']} → {p['to']}: {p['reason']}"))
        elif k == "model.retry":
            self.line(a, dim(f"retry {p['attempt']} on {p['backend']}: {clip(p['error'], 120)}"))
        elif k == "model.call" and self.verbose and p.get("ok"):
            tps = f" {p['tok_s']} tok/s" if p.get("tok_s") else ""
            self.line(a, dim(f"{p['backend']}/{p['model']}: {p['prompt_tokens']}+{p['completion_tokens']} tokens, {p['seconds']}s{tps}"))
        elif k.startswith("backend."):
            self.line(None, yellow(f"backend {p['backend']} is {k.split('.')[1]}{': ' + p['reason'] if p.get('reason') else ''}"))
        elif k == "run.attention":
            self.line(None, yellow(f"⚠ {p['message']}"))
        elif k == "checkpoint.created" and self.verbose:
            self.line(None, dim(f"checkpoint {p['id']}: {p['label']}"))
        elif k == "run.finished":
            s = p["stats"]
            color = green if p["status"] == "done" else yellow
            self.line(None, color(f"■ run {p['status']} in {s['seconds']}s · {s['tokens']} tokens · {s['turns']} turns"))
            if p.get("reason"):
                out(f"  {p['reason']}")
            if p.get("summary"):
                out(bold("Deliverable:"))
                out("  " + p["summary"].strip().replace("\n", "\n  "))


# --- interaction while a run is going --------------------------------------------------------


class Console:
    """Answers approvals and attention prompts on the terminal (one at a time)."""

    def __init__(self, run: Run, interactive: bool):
        self.run = run
        self.interactive = interactive
        self.lock = asyncio.Lock()

    async def ask(self, prompt: str) -> str:
        async with self.lock:
            try:
                return (await asyncio.to_thread(input, prompt)).strip()
            except EOFError:
                return ""

    async def approval(self, ev: Event) -> None:
        p = ev.payload
        if not self.interactive:
            await self.run.approvals.decide(p["id"], False, "cli", "no terminal to ask on; use --approve all to allow")
            return
        detail = p["args"].get("command") or p["args"].get("path") or json.dumps(p["args"])
        ans = await self.ask(yellow(f"\nAllow {ev.agent} to {p['tool']}: {detail}? [y/N or a reason to refuse] "))
        if p["id"] not in self.run.approvals.pending:
            return
        if ans.lower() in ("y", "yes"):
            await self.run.approvals.decide(p["id"], True, "user")
        else:
            await self.run.approvals.decide(p["id"], False, "user", "" if ans.lower() in ("", "n", "no") else ans)

    async def attention(self, ev: Event) -> None:
        if not self.interactive:
            await self.run.finish("needs_attention", reason=ev.payload["message"])
            return
        ans = await self.ask(yellow("[r]esume, [s]top, or type a message for the team: "))
        if self.run.done.is_set():
            return
        if ans.lower() in ("s", "stop"):
            await self.run.stop()
        elif ans.lower() in ("r", "resume", ""):
            await self.run.resume()
        else:
            await self.run.user_message("#channel", ans)
            await self.run.resume()


async def _run(args: argparse.Namespace) -> int:
    args.mode_given = any(a == "--mode" or a.startswith("--mode=") for a in sys.argv)
    settings = _settings(args)
    app = await Hive.open(settings)
    run: Run | None = None
    try:
        from .modes import MODES

        mode = args.mode
        if args.project:
            project = await app.store.get_project(args.project)
            if not project:
                raise ConfigError(f"no project {args.project!r}")
        else:
            project = await app.create_project(args.goal, name=args.name, workspace=args.workspace)
        members: list[Any] = [m for m in (args.team or "").split(",") if m.strip()] or list(MODES[mode].default_team)
        if args.team_file:
            import yaml

            data = yaml.safe_load(Path(args.team_file).read_text(encoding="utf-8"))
            members = data.get("team", data) if isinstance(data, dict) else data
            if not isinstance(members, list):
                raise ConfigError(f"{args.team_file}: expected a list of agents (or a 'team:' list)")
            mode = data.get("mode", mode) if isinstance(data, dict) and not args.mode_given else mode
        team = app.build_team(members, backend=args.backend, model=args.model)
        limits: dict[str, Any] = {}
        if args.max_turns:
            limits["max_turns_per_run"] = args.max_turns
        if args.max_tokens:
            limits["max_tokens_per_run"] = args.max_tokens
        if args.max_minutes:
            limits["max_wall_seconds"] = int(args.max_minutes * 60)
        interactive = sys.stdin.isatty()
        approval = args.approve or ("ask" if interactive else "none")
        printer = Printer(verbose=args.verbose_events, stream=args.stream)
        sub = app.subscribe()
        run = await app.start_run(project, goal=args.goal, mode=mode, team=team, limits=limits, approval=approval)
        console = Console(run, interactive)
        out(dim(f"run {run.id} · project {project['slug']} · Ctrl-C stops the run"))

        async def pump() -> None:
            async for ev in sub:
                if ev.run_id != run.id:
                    continue
                if not args.quiet or ev.kind in ("run.finished", "run.attention", "approval.requested"):
                    printer(ev)
                if ev.kind == "approval.requested":
                    run.spawn(console.approval(ev))
                elif ev.kind == "run.attention":
                    run.spawn(console.attention(ev))
                elif ev.kind == "run.finished":
                    return

        pump_task = asyncio.create_task(pump())
        try:
            await run.wait()
            await asyncio.wait_for(pump_task, 5)
        finally:
            pump_task.cancel()
            sub.close()
        return 0 if run.status == "done" else 1
    except asyncio.CancelledError:
        if run and not run.done.is_set():
            await run.stop("interrupted with Ctrl-C")
        raise
    finally:
        await app.close()


async def _runs(args: argparse.Namespace) -> int:
    app = await Hive.open(_settings(args), start_health=False)
    try:
        rows = await app.store.list_runs(limit=args.limit)
        if not rows:
            out("No runs yet. Try: hive run \"write a haiku about bees to haiku.txt\"")
        for r in rows:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["started_at"]))
            st = r["status"]
            color = green if st == "done" else (red if st == "failed" else yellow)
            out(f"{bold(r['id'])}  {when}  {color(st):<20} {r['mode']:<9} {clip(r['goal'], 60)}")
    finally:
        await app.close()
    return 0


async def _show(args: argparse.Namespace) -> int:
    app = await Hive.open(_settings(args), start_health=False)
    try:
        r = await app.store.get_run(args.run)
        if not r:
            err(red(f"no run {args.run!r}"))
            return 1
        project = await app.store.get_project(r["project_id"])
        out(bold(f"Run {r['id']}") + f"  {r['status']}  ({r['mode']})")
        out(f"Goal: {r['goal']}")
        out(f"Workspace: {project['workspace'] if project else '?'}")
        out("Team: " + ", ".join(f"{a['name']} ({a['role']} on {a['backend']}{'/' + a['model'] if a.get('model') else ''})" for a in r["config"]["team"]))
        if r["stats"]:
            s = r["stats"]
            out(f"Stats: {s.get('turns')} turns, {s.get('tokens')} tokens, {s.get('seconds')}s")
        tasks = await app.store.tasks(r["id"])
        if tasks:
            out(bold("\nTasks"))
            for t in tasks:
                out(f"  #{t['id']} [{t['status']}] {t['title']}" + (f" @{t['owner']}" if t.get("owner") else ""))
        board = await app.store.board(r["id"])
        if board:
            out(bold("\nBoard"))
            for e in board:
                out(f"  [{e['kind']}] {e['title']}: {clip(e['content'], 200)}")
        if r.get("summary"):
            out(bold("\nDeliverable"))
            out("  " + r["summary"].replace("\n", "\n  "))
    finally:
        await app.close()
    return 0


async def _projects(args: argparse.Namespace) -> int:
    app = await Hive.open(_settings(args), start_health=False)
    try:
        for p in await app.store.list_projects():
            out(f"{bold(p['slug'])}  {clip(p['goal'], 60)}  {dim(p['workspace'])}")
    finally:
        await app.close()
    return 0


def _roles(args: argparse.Namespace) -> int:
    from .agents.roles import list_roles

    roles = list_roles(Path(args.config) if args.config else None)
    if args.name:
        if args.name not in roles:
            err(red(f"no role {args.name!r}"))
            return 1
        import yaml

        out(yaml.safe_dump(roles[args.name].model_dump(), sort_keys=False, allow_unicode=True))
        return 0
    for name, r in sorted(roles.items()):
        out(f"{bold(name):<22} {r.description}")
        out(dim(f"{'':14} tools: {', '.join(r.tools)}"))
    return 0


async def _serve(args: argparse.Namespace) -> int:
    import secrets

    from .web.app import is_loopback, serve

    settings = _settings(args)
    host = args.host or settings.hive.server.host
    port = args.port or settings.hive.server.port
    token = args.token or settings.hive.server.token
    if not is_loopback(host) and not token:
        token = secrets.token_urlsafe(18)
    app = await Hive.open(settings)
    shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{shown}:{port}/" + (f"?token={token}" if token else "")
    out(green("●") + f" Hive is running at {bold(url)}  (Ctrl-C stops it)")
    if not is_loopback(host):
        out(yellow("  Listening on your network. Anyone with that link can steer your agents and approve shell commands."))
    if args.open:
        import webbrowser

        webbrowser.open(url)
    try:
        await serve(app, host, port, token)
    finally:
        await app.close()
    return 0


def register(sub: Any) -> None:
    from .modes import MODES

    s = sub.add_parser("run", help="run a team on a goal and watch it work")
    s.add_argument("goal", help="what the team should do, in plain English")
    s.add_argument("--mode", default=next(iter(MODES)) if "pipeline" not in MODES else "pipeline", choices=sorted(MODES),
                   help="collaboration mode (default: %(default)s)")
    s.add_argument("--team", help="comma-separated: role, name=role, role@backend or role@backend:model")
    s.add_argument("--team-file", help="YAML list of agents (role, name, backend, model, tools, system_prompt, ...)")
    s.add_argument("--backend", "-b", help="backend for agents that don't name one")
    s.add_argument("--model", "-m", help="model for agents on that backend")
    s.add_argument("--workspace", "-w", help="project folder (default: a new folder in Hive's data dir)")
    s.add_argument("--project", help="continue in an existing project's folder (slug or id)")
    s.add_argument("--name", help="project name (default: from the goal)")
    s.add_argument("--approve", choices=["ask", "all", "none"], help="risky actions: ask (default in a terminal), all, none")
    s.add_argument("--max-turns", type=int, help="turn budget for the run")
    s.add_argument("--max-tokens", type=int, help="token budget for the run")
    s.add_argument("--max-minutes", type=float, help="time budget for the run")
    s.add_argument("--stream", action="store_true", help="print model output as it streams")
    s.add_argument("--quiet", "-q", action="store_true", help="only print the result")
    s.add_argument("--details", dest="verbose_events", action="store_true", help="print full tool output and model stats")
    s.set_defaults(func=_run, mode_given=False)

    s = sub.add_parser("serve", help="start the web UI")
    s.add_argument("--host", help="address to listen on (default: 127.0.0.1; anything else needs a token)")
    s.add_argument("--port", type=int, help="port (default: 8765)")
    s.add_argument("--token", help="access token (generated when listening beyond localhost)")
    s.add_argument("--open", action="store_true", help="open it in your browser")
    s.set_defaults(func=_serve)

    s = sub.add_parser("runs", help="list recent runs")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=_runs)

    s = sub.add_parser("show", help="show a run's tasks, board and result")
    s.add_argument("run", help="run id (a prefix is enough)")
    s.set_defaults(func=_show)

    s = sub.add_parser("projects", help="list projects")
    s.set_defaults(func=_projects)

    s = sub.add_parser("roles", help="list role templates, or show one")
    s.add_argument("name", nargs="?")
    s.set_defaults(func=_roles)


_EXTRA_COMMANDS.append(register)
