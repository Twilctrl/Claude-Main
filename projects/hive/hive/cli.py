"""The ``hive`` command."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

from . import __version__, paths
from .backends import BackendError, ChatMessage, GenParams
from .config import ConfigError, Route, Settings, load_settings, ollama_default_url
from .scheduler import PRIORITY_INTERACTIVE, Scheduler

# --- output helpers ------------------------------------------------------------------

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    """Wrap ``text`` in an ANSI colour code when writing to a terminal."""
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def dim(t: str) -> str:
    return c(t, "2")


def bold(t: str) -> str:
    return c(t, "1")


def red(t: str) -> str:
    return c(t, "31")


def green(t: str) -> str:
    return c(t, "32")


def yellow(t: str) -> str:
    return c(t, "33")


def out(*parts: Any, end: str = "\n") -> None:
    text = " ".join(str(p) for p in parts)
    try:
        sys.stdout.write(text + end)
    except UnicodeEncodeError:
        sys.stdout.write(text.encode("ascii", "replace").decode() + end)
    sys.stdout.flush()


def err(*parts: Any) -> None:
    print(*parts, file=sys.stderr)


def _settings(args: argparse.Namespace) -> Settings:
    return load_settings(Path(args.config) if args.config else None)


# --- hive init -----------------------------------------------------------------------

_PREFERRED = ["llama3.1", "llama3.2", "granite4", "granite3", "phi4", "gpt-oss", "nemotron", "hermes3", "mistral", "qwen", "gemma3"]
_NOT_CHAT = ("embed", "bge", "minilm", "whisper", "nomic", "rerank", "moondream", "vision", "llava")


def pick_model(names: list[str]) -> str | None:
    """Choose a sensible default chat model from what Ollama has installed."""
    chat = [n for n in names if not any(x in n.lower() for x in _NOT_CHAT)]
    for pref in _PREFERRED:
        for n in chat:
            if n.lower().startswith(pref):
                return n
    return chat[0] if chat else None


HIVE_YAML_TEMPLATE = """\
# Hive settings. Every key is optional; these are the defaults.
default_backend: {default_backend}

# summarizer: local          # backend (or {{backend: x, model: y}}) that condenses long histories
max_tokens: 1024             # reply budget per model call
approval_timeout: 900        # seconds before an unanswered approval counts as "reject" (0 = wait)
backend_down_timeout: 600    # seconds an agent waits for a dead backend before the run pauses

limits:                      # loop protection, per run
  max_turns_per_agent: 30
  max_turns_per_run: 150
  max_tool_steps: 8
  max_tokens_per_run: 400000
  max_wall_seconds: 7200
  repeat_threshold: 3
  stall_turns: 12
  pingpong_messages: 6
  protocol_failures: 4
  max_rounds: 3              # pipeline review rounds, manager review cycles
  debate_rounds: 1           # critique + revise rounds before the judge

shell:                       # only for agents you give the run_shell tool
  timeout: 60
  max_output: 64000
  sandbox: auto              # auto: use bubblewrap if installed (Linux) | bwrap: require it | none
  allow: []                  # command prefixes that run without asking, e.g. ["pytest", "python -m pytest"]

web_fetch:                   # only for agents you give the web_fetch tool
  allowed_hosts: []          # empty: any host
  max_bytes: 500000

server:
  host: 127.0.0.1            # anything else requires a token
  port: 8765
"""


def cmd_init(args: argparse.Namespace) -> int:
    cdir = Path(args.config) if args.config else paths.config_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "roles").mkdir(exist_ok=True)
    (cdir / "tools").mkdir(exist_ok=True)
    bpath, hpath = cdir / "backends.yaml", cdir / "hive.yaml"

    url = args.url or ollama_default_url()
    model, names = None, []
    try:
        r = httpx.get(f"{url.rstrip('/')}/api/tags", timeout=3)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        model = args.model or pick_model(names)
        out(green("✓"), f"found Ollama at {url} with {len(names)} model(s)")
    except (httpx.HTTPError, ValueError):
        out(yellow("!"), f"no Ollama answering at {url}. Using the built-in mock backend for now.")

    if bpath.exists() and not args.force:
        out(dim(f"  {bpath} already exists; leaving it alone (use --force to overwrite)"))
        default_backend = load_settings(cdir).default_backend()
    else:
        backend: dict[str, Any] = {
            "name": "local",
            "type": "ollama",
            "url": url,
            "default_model": model or "llama3.2:3b",
            "concurrency": 1,
            "context": 8192,
        }
        doc = {"backends": [backend]}
        header = (
            "# Model servers Hive can use. See the README for multi-machine examples.\n"
            "# concurrency: how many requests the server takes at once (match OLLAMA_NUM_PARALLEL)\n"
            "# context: tokens per request; Hive sends it to Ollama as num_ctx\n"
            "# A built-in 'mock' backend (no model needed) is always available too.\n"
        )
        if not names:
            header += "# Ollama wasn't running when this was written: start it, then `hive backends --check`.\n"
        bpath.write_text(header + yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        out(green("✓"), f"wrote {bpath}")
        if names and not model:
            out(yellow("!"), "Ollama has no chat model yet. Try: ollama pull llama3.2:3b")
        default_backend = "local" if names else "mock"

    if hpath.exists() and not args.force:
        out(dim(f"  {hpath} already exists; leaving it alone"))
    else:
        hpath.write_text(HIVE_YAML_TEMPLATE.format(default_backend=default_backend), encoding="utf-8")
        out(green("✓"), f"wrote {hpath}")
    (cdir / "tools" / "README.txt").write_text(
        "Drop Python files here to add tools. See the Hive README, 'Adding a tool'.\n"
        "Only this folder is scanned: never a project workspace.\n",
        encoding="utf-8",
    )
    (cdir / "roles" / "README.txt").write_text(
        "A YAML file here with the same name as a built-in role (planner.yaml, coder.yaml, ...)\n"
        "replaces it. New names add new roles. `hive roles show coder` prints a built-in to start from.\n",
        encoding="utf-8",
    )
    out()
    out("Next:")
    out("  hive backends --check      # are the model servers up?")
    out('  hive chat                  # talk to one model')
    out('  hive run "write a haiku about bees to haiku.txt"')
    return 0


# --- hive backends ----------------------------------------------------------------------


async def _backends(args: argparse.Namespace) -> int:
    settings = _settings(args)
    sched = Scheduler.from_settings(settings)
    try:
        health = await sched.check_health(force=True) if args.check else {}
        for b in settings.backends:
            backend = sched.backend(b.name)
            line = f"{bold(b.name):<20} {b.type:<8} {b.url or '-':<32} model={b.default_model or '-'} concurrency={b.concurrency} context={b.context}"
            out(line)
            if b.name in health:
                h = health[b.name]
                if h.ok:
                    lat = f"{h.latency_ms:.0f} ms" if h.latency_ms is not None else ""
                    out("   ", green("up"), dim(f"{lat} {h.detail}"), dim(f"loaded: {', '.join(h.loaded)}") if h.loaded else "")
                    if args.models:
                        for m in await backend.list_models():
                            out("     ", m.name)
                    if b.default_model:
                        caps = await backend.probe(b.default_model)
                        tools = "native tools" if caps.tool_calling else "text tool protocol"
                        out("   ", dim(f"{b.default_model}: context {caps.max_context}, {tools}"))
                else:
                    out("   ", red("down"), dim(h.detail))
    finally:
        await sched.close()
    return 0


# --- hive chat ----------------------------------------------------------------------------


def _fit(history: list[ChatMessage], budget_chars: int) -> list[ChatMessage]:
    """Keep the system prompt and as many recent turns as fit."""
    system = [m for m in history if m.role == "system"]
    rest = [m for m in history if m.role != "system"]
    used = sum(len(m.content) for m in system)
    kept: list[ChatMessage] = []
    for m in reversed(rest):
        if kept and used + len(m.content) > budget_chars:
            break
        kept.append(m)
        used += len(m.content)
    return system + list(reversed(kept))


async def _chat(args: argparse.Namespace) -> int:
    settings = _settings(args)
    backend_name = args.backend or settings.default_backend()
    bconf = settings.backend(backend_name)
    sched = Scheduler.from_settings(settings)
    route = Route(backend=backend_name, model=args.model)
    model = sched.model_for(route)
    system = args.system
    if args.role:
        from .agents.roles import load_role

        system = load_role(args.role, settings.config_dir).system_prompt
    history: list[ChatMessage] = [ChatMessage("system", system)] if system else []
    budget = int(bconf.context * bconf.chars_per_token * 0.75)
    params = GenParams(temperature=args.temperature, max_tokens=settings.hive.max_tokens)

    def on_event(kind: str, data: dict[str, Any]) -> None:
        if kind == "queued":
            out(dim(f"[queued on {data['backend']}]"))
        elif kind == "retry":
            out(dim(f"\n[retry {data['attempt']} on {data['backend']} in {data['delay']}s: {data['error']}]"))
        elif kind == "stream_reset":
            out(dim("\n[restarting reply]"))
        elif kind == "rerouted":
            out(yellow(f"[rerouted {data['from']} → {data['to']}: {data['reason']}]"))
        elif kind == "waiting_backend":
            out(yellow(f"[waiting for {', '.join(data['backends'])} to come back]"))

    async def ask(text: str) -> bool:
        history.append(ChatMessage("user", text))
        stream = not args.no_stream
        if stream:
            out(bold(f"{model}> "), end="")
        try:
            result = await sched.chat(
                route,
                _fit(history, budget),
                params=params,
                priority=PRIORITY_INTERACTIVE,
                fallbacks=bconf.fallbacks,
                on_delta=(lambda t: out(t, end="")) if stream else None,
                on_event=on_event,
            )
        except BackendError as e:
            history.pop()
            out()
            err(red(f"error: {e}"))
            return False
        if stream:
            out()
        else:
            out(bold(f"{model}> ") + result.text)
        history.append(ChatMessage("assistant", result.text))
        tps = f", {result.tokens_per_sec:.1f} tok/s" if result.tokens_per_sec else ""
        out(dim(f"[{result.backend} · {result.usage.completion_tokens} tokens · {result.total_seconds:.1f}s{tps}]"))
        return True

    try:
        if args.once:
            return 0 if await ask(args.once) else 1
        out(dim(f"Chatting with {model} on {backend_name}. /reset clears history, /exit or Ctrl-D quits."))
        while True:
            try:
                line = await asyncio.to_thread(input, "you> ")
            except EOFError:
                out()
                return 0
            line = line.strip()
            if not line:
                continue
            if line in ("/exit", "/quit"):
                return 0
            if line == "/reset":
                history[:] = [m for m in history if m.role == "system"]
                out(dim("[history cleared]"))
                continue
            await ask(line)
    finally:
        await sched.close()


# --- entry point ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hive", description="Run local AI agents together on a shared project.")
    p.add_argument("--version", action="version", version=f"hive {__version__}")
    p.add_argument("--config", help="config directory (default: %(default)s)", default=None)
    p.add_argument("-v", "--verbose", action="store_true", help="log debug output")
    sub = p.add_subparsers(dest="cmd", metavar="command")

    s = sub.add_parser("init", help="write a starter config, detecting a local Ollama")
    s.add_argument("--url", help="Ollama URL (default: $OLLAMA_HOST or http://127.0.0.1:11434)")
    s.add_argument("--model", help="default model (default: picked from what Ollama has)")
    s.add_argument("--force", action="store_true", help="overwrite existing config files")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("backends", help="list backends; --check probes them")
    s.add_argument("--check", action="store_true", help="probe each backend's health")
    s.add_argument("--models", action="store_true", help="with --check, list each backend's models")
    s.set_defaults(func=_backends)

    s = sub.add_parser("chat", help="chat with one model")
    s.add_argument("--backend", "-b", help="backend name (default: the default backend)")
    s.add_argument("--model", "-m", help="model (default: the backend's default_model)")
    s.add_argument("--system", "-s", help="system prompt")
    s.add_argument("--role", "-r", help="use a role template's system prompt")
    s.add_argument("--temperature", "-t", type=float, default=None)
    s.add_argument("--once", metavar="PROMPT", help="ask one question, print the reply, exit")
    s.add_argument("--no-stream", action="store_true", help="print the reply when it's complete")
    s.set_defaults(func=_chat)

    from . import cli_runs  # noqa: F401  (adds its commands to _EXTRA_COMMANDS)

    for register in _EXTRA_COMMANDS:
        register(sub)
    return p


#: Later modules add subcommands here: ``fn(subparsers)``.
_EXTRA_COMMANDS: list[Any] = []


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        result = args.func(args)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        return int(result or 0)
    except ConfigError as e:
        err(red(f"config error: {e}"))
        return 2
    except KeyboardInterrupt:
        err(dim("\ninterrupted"))
        return 130
