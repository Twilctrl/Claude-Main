# Hive

Run several local AI models at once and let them work together on a project.
A planner breaks the goal down, a coder writes files, a reviewer sends work
back, a tester checks it. Or several agents debate and a judge decides. Or a
coordinator hands tasks to workers that run in parallel. You watch, steer,
approve risky steps and roll back from a terminal or a web page.

Everything is offline. Hive talks only to model servers you run yourself
(Ollama, hailo-ollama, llama.cpp, LM Studio, vLLM), on this machine or
elsewhere on your LAN. It's built to run on modest hardware: a Raspberry Pi 5
with a Hailo NPU is a first-class target, alongside a desktop with a big GPU.

```
  you ──▶ hive run / web UI ──▶ team: planner → coder → reviewer → tester
                                     │ tasks, messages, shared files, board
                                     ▼
                        scheduler: one queue per device, never overloads it
                     ┌──────────────┼──────────────────┐
                Ollama (GPU)   hailo-ollama (Pi NPU)   llama.cpp / LM Studio
```

The design, with the reasoning behind each choice, is in
[docs/DESIGN.md](docs/DESIGN.md).

> **Status.** Everything below works and is covered by about 190 tests, but
> those tests use recorded server responses and a built-in fake model.
> Hive hasn't yet been run against a real Ollama or llama.cpp server. See
> [What's not done](#whats-not-done) and try it on your own hardware first.

## Contents

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Configuring backends](#configuring-backends)
- [Teams and roles](#teams-and-roles)
- [Collaboration modes](#collaboration-modes)
- [The web UI](#the-web-ui)
- [Command reference](#command-reference)
- [Loop protection and budgets](#loop-protection-and-budgets)
- [Tools](#tools)
- [Adding a backend, tool, role or mode](#adding-a-backend-tool-role-or-mode)
- [Security](#security)
- [Running on different hardware](#running-on-different-hardware)
- [Notifications](#notifications)
- [Persistence, replay and checkpoints](#persistence-replay-and-checkpoints)
- [Development](#development)
- [What's not done](#whats-not-done)

## Quick start

You need Python 3.11 or newer, plus [Ollama](https://ollama.com) with at least
one chat model pulled (for example `ollama pull llama3.1:8b`). Hive installs
into its own environment and never touches system packages.

```
cd projects/hive
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .              # or: pipx install .   /   uv tool install .

hive init                     # finds Ollama on localhost, picks a model, writes the config
hive backends --check         # is everything up?
hive run "Write a Python script that prints the 10 most common words in a text file, with a README"
hive serve --open             # the web UI, on http://127.0.0.1:8765
```

No model handy? Every command works with the built-in `mock` backend, which
runs a rule-based fake model:

```
hive run "write app.py and README.md" -b mock
hive run "pick a project name, write it to name.txt" --mode debate -b mock
hive run "write api.py and docs.md" --mode manager -b mock
```

`hive run` prints the team working: who's thinking, every tool call, every
file change, and the result at the end. Ctrl-C stops the run. In a terminal,
risky actions (shell commands, deletes) ask you first.

On immutable distros (Fedora Atomic, Bazzite and so on) you can use the
[Containerfile](Containerfile) instead. See
[Running on different hardware](#running-on-different-hardware).

## How it works

- **Backends.** Each backend is a model server, or one device behind a
  server. Each has its own queue and concurrency limit, so a slow device never
  gets more requests than it can take.
- **Agents.** Each agent has a role (Planner, Coder, Reviewer, Researcher,
  Writer, Tester, Coordinator or Judge), a backend and model, a system prompt,
  sampling settings, a list of allowed tools, and a context budget. Each one
  is an independent async worker with its own inbox.
- **The shared project.** Agents work in one project folder (the workspace)
  and share:
  - a **task board**: tasks with an owner, status, dependencies and a result
  - a **blackboard**: notes, decisions, artifacts and the final deliverable
  - **messages**: direct, or to the whole team channel

  Agents act on all of these only through tools, so every action is checked
  against their permissions and logged.
- **A mode** is the conductor. It hands out work and decides when the goal is
  met: pipeline, debate, manager/workers, or solo.
- **The event log.** Every change is written to a SQLite event log first. The
  terminal output, the web UI, replay, rollback and the Markdown export all
  read from it.

Small models get special care:
- **Text tool protocol.** Models without native tool calling (like the Pi's
  NPU models) use a one-action-per-reply JSON protocol, read leniently, with
  re-prompts when a reply is malformed.
- **Context window.** History that outgrows the window is folded into a
  running summary, or dropped on very small windows. Hive always sends Ollama
  `num_ctx`, because Ollama otherwise silently truncates to a smaller default.
- **Loop guard.** It stops agents that repeat themselves, stall, or message
  each other in circles.

## Configuring backends

Hive reads two files from its config folder:

| OS | Config | Data (database, default workspaces) |
| --- | --- | --- |
| Linux | `~/.config/hive/` | `~/.local/share/hive/` |
| Windows | `%APPDATA%\hive\` | `%LOCALAPPDATA%\hive\` |
| macOS | `~/Library/Application Support/hive/` | same |

To use other folders, set `HIVE_CONFIG_DIR` and `HIVE_DATA_DIR`, or pass
`hive --config DIR`.

- `backends.yaml` lists the model servers. Without one, Hive assumes Ollama
  on localhost.
- `hive.yaml` holds the defaults: limits, shell and web settings, the UI port.
  `hive init` writes it with every option explained.

### One machine

```yaml
# backends.yaml
backends:
  - name: local
    type: ollama
    url: http://127.0.0.1:11434
    default_model: llama3.1:8b
    concurrency: 1        # match OLLAMA_NUM_PARALLEL
    context: 8192         # sent to Ollama as num_ctx
```

### Several machines on a LAN

This example has a desktop GPU running Ollama and llama.cpp, and a Pi 5 with
a Hailo-10H running [CerberOS](../cerberos):

```yaml
backends:
  - name: desktop
    type: ollama
    url: http://192.168.1.50:11434
    default_model: qwen2.5-coder:14b
    concurrency: 2
    context: 16384
    device: desktop-gpu          # shares a limit with the next entry

  - name: desktop-llamacpp
    type: openai                 # llama.cpp server, LM Studio, vLLM
    url: http://192.168.1.50:8080/v1
    default_model: gpt-oss-20b
    concurrency: 1
    context: 32768
    device: desktop-gpu

  - name: pi-npu                 # CerberOS gate: "@npu" routes to hailo-ollama
    type: ollama
    url: http://pi5.local:11434
    default_model: llama3.2:1b@npu
    concurrency: 1
    context: 2048
    tools: false                 # no native tool calling: use the text protocol
    fallbacks: [pi-cpu, desktop] # if the NPU is down, reroute
    timeouts: {first_token: 180, total: 900}

  - name: pi-cpu
    type: ollama
    url: http://pi5.local:11434
    default_model: gemma3:1b@cpu
    concurrency: 1
    context: 4096
    tools: false

devices:
  desktop-gpu: {concurrency: 2}  # both desktop backends together
```

The full file, with a variant that talks to hailo-ollama directly (no
CerberOS), is [examples/backends.lan.yaml](examples/backends.lan.yaml).

Every backend field:

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | required | What agents and the UI call it |
| `type` | `ollama` | `ollama` (also `hailo-ollama`), `openai` (also `llamacpp`, `lmstudio`, `vllm`), `mock`, or `package.module:Class` |
| `url` | | Server address. For `openai` it may end in `/v1` or not |
| `default_model` | | Model used when an agent doesn't name one |
| `concurrency` | 1 | Requests this backend takes at once. Hive queues the rest |
| `context` | 4096 | Context window in tokens (Ollama's `num_ctx`). Probed and clamped for Ollama models that report it |
| `tools` | auto | Native tool calling. `false` uses the text protocol. Unset means "ask Ollama", or yes for others |
| `device` | | Backends sharing a device also share its limit (`devices:` sets it; default: the largest member's) |
| `fallbacks` | `[]` | Backends (or `{backend, model}`) to reroute to while this one is down |
| `timeouts` | 10/120/60/600 | Seconds for `connect`, `first_token` (covers model loading), `idle` between tokens, and `total` |
| `retries` | 2 | Retries on connection errors, timeouts, 429 and 5xx |
| `speed` | 1.0 | Relative speed, shown in the UI |
| `keep_alive` | | Passed to Ollama, e.g. `30m` |
| `api_key` / `api_key_env` | | Bearer token, or the name of an environment variable holding it |
| `probe` | true | Ask Ollama's `/api/show` about models. Set false for hailo-ollama |
| `chars_per_token` | 3.5 | Starting point for token estimates, corrected from server reports |
| `models` | | Per-model overrides: `{name: {context, tools, speed}}` |

How the scheduler handles trouble:
- **Retries.** A failed call is retried with backoff.
- **Down backends.** Three failures in a row mark a backend down. Its agents
  reroute to a healthy fallback, or wait (status "waiting on backend") and
  carry on when a health probe sees it come back.
- **Model affinity.** On one-model-at-a-time servers, the queue prefers the
  model that's already loaded, so the server doesn't reload a model on every
  call.

`hive backends --check --models` shows what each server has.

## Teams and roles

A team is a list of agents. On the command line:

```
hive run "..." --team planner,coder,reviewer                  # roles, all on the default backend
hive run "..." --team planner@desktop,coder@desktop:qwen2.5-coder:14b,tester@pi-cpu
hive run "..." --team lead=coordinator@desktop,coder,coder    # name=role; repeats become coder2
```

For full control (prompts, tools, sampling), use a team file:

```yaml
# hive run --team-file team.yaml "..."
mode: pipeline
team:
  - {role: planner, backend: desktop}
  - {role: coder, backend: desktop, model: "qwen2.5-coder:14b"}
  - name: tester
    role: tester
    backend: pi-cpu
    tools: [list_files, read_file, write_file, run_shell, approve, request_changes]
    params: {temperature: 0}
    system_prompt: |
      You are the Tester. ...
```

The built-in roles are short, imperative prompts, written that way because
1–3B models follow them best. `hive roles` lists them and `hive roles show
coder` prints one. To change a role, copy it into `<config>/roles/coder.yaml`
and edit it. To add one, put a new file there (see
[examples/roles/security-reviewer.yaml](examples/roles/security-reviewer.yaml)).

| Role | Does | Tools by default |
| --- | --- | --- |
| planner | Breaks the goal into tasks | files (read), board, `create_task` |
| coder | Writes and edits files | files, board, messages |
| reviewer | Approves or requests changes | files (read), `approve`, `request_changes` |
| tester | Checks it works (runs it if allowed `run_shell`) | files, `approve`, `request_changes` |
| researcher | Gathers facts into notes | files (read), board |
| writer | Writes prose documents | files, board |
| coordinator | Delegates tasks, checks results, finishes the run | files (read), tasks, `finish_run` |
| judge | Picks or merges debate proposals | files, `record_decision` |

Put the planner, coordinator and judge on your strongest model. Give the
small models narrow jobs.

## Collaboration modes

| Mode | Default team | How it goes |
| --- | --- | --- |
| `pipeline` (default) | planner, coder, reviewer, tester | Stages in team order. The planner's tasks go to the first maker one at a time, in dependency order. Each check stage gets the diff and must `approve` or `request_changes`. Changes go back to the maker, for up to `max_rounds` rounds. Done when the last check approves |
| `debate` | coder, coder, judge | Everyone but the judge proposes in parallel, then critiques the others and revises (`debate_rounds` times). The judge picks or merges, and writes files if the goal names any |
| `manager` | coordinator, coder, writer | The coordinator creates tasks and assigns them. Workers run in parallel as dependencies allow, and idle workers pick up unassigned tasks. When everything is done the coordinator checks the results and calls `finish_run`, or adds follow-ups |
| `solo` | coder | One agent takes the whole goal. Teammates answer its messages |

Each mode has safety nets for small models:
- A reviewer who replies in prose has its verdict read from the text.
- A coordinator who never creates tasks gets reminded once; after that, the
  goal goes to a worker as a single task.
- A team that goes idle without finishing ends as `needs_attention` instead
  of hanging.

Every run ends with a **deliverable** on the board: a summary and the files
changed.

## The web UI

`hive serve` starts it on http://127.0.0.1:8765 (`--open` opens your
browser). Pages:

- **Runs.** Every run, with its status and usage, plus live backend load.
- **New run.** The goal, the mode, and a team builder (role, name, backend,
  and model, with each server's models suggested). Also the project folder,
  the approval policy and budgets.
- **Run view.**
  - **Agent cards:** status (idle, thinking, waiting on backend, waiting for
    approval, paused), live output as it streams, turns and tokens.
  - **Agent controls:** pause, resume, interrupt, edit the prompt or model
    mid-run, message.
  - **Tabs:** a **feed** of everything that happened, a **task board**, the
    **blackboard**, **files** (every version as a diff, filterable by
    agent), and **checkpoints**.
  - **Approvals:** risky actions wait in a panel. Allow or refuse them, with
    an optional reason the agent will see.
  - **Steering:** message one agent or everyone, pause or resume all, take a
    checkpoint, **roll back** to any checkpoint (files, tasks, board and agent
    memory all go back), stop.
  - **Backend load:** each device's busy slots, queue depth and tokens/sec,
    with the bottleneck flagged.
  - **Budget meters:** tokens, turns and time.
  - **Replay:** a scrubber over the whole event log, at 1×, 4×, 16× or 64×.
  - **Export .md:** the run as a Markdown transcript.
  - **Resume:** for runs that were interrupted, stopped, or ran out of
    budget.

The UI is Preact + htm, vendored (about 13 KB). There's no build step, no
Node, and nothing loaded from the internet.

## Command reference

| Command | What it does |
| --- | --- |
| `hive init [--url URL] [--model M] [--force]` | Write `backends.yaml` and `hive.yaml`, detecting Ollama (honours `OLLAMA_HOST`) |
| `hive backends [--check] [--models]` | List backends, probe their health, list models |
| `hive chat [-b BACKEND] [-m MODEL] [-r ROLE] [--once TEXT]` | Talk to one model (`/reset`, `/exit`) |
| `hive run "goal" [--mode M] [--team ...] [--team-file F] [-b B] [-m M] [-w DIR] [--project P] [--approve ask\|all\|none] [--max-turns N] [--max-tokens N] [--max-minutes N] [--stream] [--details] [-q]` | Run a team and watch it |
| `hive serve [--host H] [--port P] [--token T] [--open]` | The web UI |
| `hive runs` / `hive show RUN` / `hive projects` | What's been run: tasks, board, deliverable |
| `hive resume RUN [--max-turns ...] [--approve ...]` | Continue an interrupted or stopped run from its last checkpoint, optionally with bigger budgets |
| `hive replay RUN [--speed X]` | Replay the log in the terminal (`--speed 0` is instant) |
| `hive export RUN [-o FILE] [--no-diffs]` | Markdown transcript: team, deliverable, tasks, timeline, diffs |
| `hive roles [NAME]` | List roles, or print one as YAML |

Run ids can be shortened to any unique prefix. `--workspace DIR` works in a
folder of yours instead of a new one in the data dir. `--project SLUG`
continues in an earlier project's folder.

## Loop protection and budgets

Small models can spin forever, so every run has limits. Set the defaults in
`hive.yaml`, or per run in the UI or with `--max-*`:

| Limit | Default | When it trips |
| --- | --- | --- |
| `max_turns_per_agent` | 30 | That agent pauses; you decide |
| `max_turns_per_run`, `max_tokens_per_run`, `max_wall_seconds` | 150, 400k, 2 h | The run stops as `budget_exhausted` (resumable with a bigger budget) |
| `max_tool_steps` | 8 | Tool calls in one turn |
| `repeat_threshold`, `repeat_similarity` | 3, 0.9 | Near-identical replies, or identical tool calls, in a row |
| `stall_turns` | 12 | Turns without progress (no file, task or board change) |
| `pingpong_messages` | 6 | Two agents messaging back and forth without progress |
| `protocol_failures` | 4 | Malformed replies in a row |
| `max_rounds` | 3 | Pipeline review rounds, manager review cycles |
| `debate_rounds` | 1 | Critique and revise rounds before the judge |

The soft rules (repeats, stalls, ping-pong, malformed replies) nudge the
agent first with a note in its context, like "You are repeating yourself...".
If that doesn't help, they pause the agent and ask you. Separately, replies
between agents stop waking the other agent after one round trip, so two
polite small models can't thank each other forever.

## Tools

| Tool | Default | Notes |
| --- | --- | --- |
| `list_files`, `read_file` (paged), `write_file`, `edit_file` (find/replace) | per role | Workspace only (see [Security](#security)). Writes are versioned |
| `delete_file` | not in any role | Always needs approval |
| `post_note`, `record_decision`, `read_board` | per role | The blackboard |
| `create_task`, `assign_task`, `claim_task`, `complete_task`, `fail_task`, `handoff_task`, `list_tasks` | per role and mode | The task board |
| `send_message`, `broadcast` | per role | Direct messages, or the team channel |
| `approve`, `request_changes`, `finish_run` | check roles, coordinator | The mode decides what they mean |
| `run_shell` | **off** | Opt in per agent. Asks before every command unless allow-listed |
| `web_fetch` | **off** | Opt in per agent. GET only, size-limited, optional host allow-list |

**Shared files.** Every write is a new version. An agent can't overwrite a
file it hasn't read, or one that changed since it read it: the write is
refused and the agent sees the other agent's diff and redoes its change.
`edit_file` applies whenever its `find` text still matches, so most
concurrent edits merge on their own. Edits you make outside Hive are noticed
and recorded as `external`.

## Adding a backend, tool, role or mode

**A tool.** Drop a Python file into `<config>/tools/`. The JSON schema comes
from the type hints and the docstring's `Args:` section:

```python
from hive.tools import ToolContext, ToolResult, tool

@tool(group="files")                     # risky=True makes it ask for approval
async def word_count(ctx: ToolContext, path: str) -> ToolResult:
    """Count the words in a text file.

    Args:
        path: the file, relative to the project folder
    """
    data = await ctx.run.files.read(ctx.agent.name, path)   # sandboxed and versioned
    return ToolResult(f"{len(data.split())} words")
```

Then list `word_count` in an agent's `tools`. A complete example is in
[examples/tools/word_count.py](examples/tools/word_count.py). Plugin errors are
logged and don't stop Hive from starting.

**A backend type.** Subclass `hive.backends.ModelBackend` and implement:
- `chat()`: an async generator yielding `TextDelta`s, then one `ChatResult`
- `list_models()`
- `health()`

Then either register it with `@register_backend("mytype")`, or put
`type: mypackage.module:MyBackend` in `backends.yaml`. The Ollama adapter
(`hive/backends/ollama.py`, about 200 lines) is a good model, including how
it maps HTTP errors to retryable and non-retryable ones.

**A role.** Add `<config>/roles/name.yaml` with `name`, `description`,
`system_prompt`, `tools`, `params`, and optionally `context_budget`,
`wake_on_broadcast` and `approval` (`ask`, `allow` or `deny` for risky tools).

**A mode.** Subclass `hive.modes.Mode`, implement the hooks you need, and add
it to `hive.modes.MODES`. The hooks are `start`, `on_final`, `on_task`,
`on_verdict`, `on_idle`, `on_restore`, `state` and `load_state`. Solo
(`hive/modes/solo.py`, 50 lines) is the smallest example.

## Security

Hive gives language models the ability to change files and, if you allow it,
run commands. Treat every model output as untrusted input, because it can be
steered by anything the model reads: a file in the workspace, a fetched web
page, another agent's message (prompt injection). Here's what Hive does about
it, and where its protections end.

**What's enforced**

- **Tool allow-lists.** An agent can only call tools in its own list. Other
  tools aren't offered to the model and are refused if requested.
- **Workspace sandbox for file tools.** Paths are relative to the project
  folder. Hive refuses paths that:
  - are absolute, or start with a drive letter or UNC prefix (`C:`, `\\server`)
  - contain `~`, `..` that leaves the folder, NUL bytes, or NTFS streams (`:`)
  - use Windows device names (`CON`, `NUL` and so on)
  - resolve outside the folder through symlinks

  Writes inside `.git/` are refused: a hook planted there would run the next
  time you use git. File sizes are capped.
- **Risky actions need approval.** By default you approve every shell command
  and file delete. Unanswered requests are refused after
  `approval_timeout` (15 minutes). In the terminal, `--approve none` refuses
  them all. `--approve all` approves them all; avoid it with shell-enabled
  agents on a machine you care about.
- **Hive's own state is outside the workspace,** so agents can't edit logs,
  checkpoints or config.
- **Plugins load only from your config folder,** never from a workspace,
  because agents can write to workspaces and could otherwise create their own
  tools.

**The shell tool is not a security boundary unless bubblewrap is active**

`run_shell` is off unless you add it to an agent's tools. When it runs:
- The working directory is the workspace and stdin is closed.
- The environment is scrubbed (no API keys or tokens), and `HOME` points to a
  scratch folder.
- A timeout kills the whole process tree, and output is capped.

On Linux with [bubblewrap](https://github.com/containers/bubblewrap)
installed (Fedora Atomic ships it; on Debian or Pi OS run
`apt install bubblewrap`), commands also run inside a sandbox:
- the whole filesystem is read-only, except the workspace;
- `/tmp` is private and empty (tools installed under `/tmp` won't be visible);
- there is no network.

Hive checks that bubblewrap actually works (some systems block the user
namespaces it needs), and every shell result says `(not sandboxed)` when it
didn't. Set `shell.sandbox: bwrap` in `hive.yaml` to refuse to run commands
at all without it.

**Without bubblewrap, including on Windows and macOS and inside most
containers, a command runs as you.** It can read your files and reach the
network. Approval is then the only safeguard, so read each command before
allowing it. For untrusted work, run Hive in a container or VM.

`shell.allow` lists command prefixes that skip approval, such as `pytest`.
A command only matches if it contains no shell operators, so
`pytest; rm -rf ~` still asks.

**web_fetch** is off by default. When on, agents can reach any host your
machine can, including LAN devices such as your router's admin page, unless
you set `web_fetch.allowed_hosts`. That list is checked on every redirect.
Fetched text can contain instructions aimed at the model.

**The web UI** listens on 127.0.0.1 only. If you bind it to anything else
(`--host 0.0.0.0`), it requires a token. Hive generates one and prints a link
containing it. Anyone with that link can steer agents and approve shell
commands. There's no TLS, so on a LAN use a network you trust, or put a TLS
reverse proxy in front. Against attacks from other websites:
- every state-changing request needs a custom header, which cross-site pages
  can't send (CSRF);
- on localhost, the server answers only to localhost names (DNS rebinding).

**Data.**
- Prompts, outputs and every file version are stored unencrypted in
  `hive.db` in the data folder. Delete it to forget everything.
- Hive sends prompts, including file contents, to the backends you
  configure, over plain HTTP unless the URL is `https`.
- Put API keys in an environment variable (`api_key_env`) rather than in
  `backends.yaml`.

## Running on different hardware

**Raspberry Pi 5 (+ Hailo NPU).** Hive itself is light: one Python process
and SQLite. It runs fine on the Pi, or on the desktop with the Pi as a
backend.
- Use `concurrency: 1` for the NPU and the CPU, with real context sizes
  (2048 for the Hailo models).
- Set `tools: false` for models without native tool calling.
- Set long `first_token` timeouts, because loading a model takes a while.
- With CerberOS, use the gate's `model@npu` / `model@cpu` names, so one URL
  gives two separate queues.
- 1B models are weak at multi-step tool use. Keep them in narrow roles
  (tester, writer, debate proposer) and put the planner or coordinator on a
  bigger machine.

**Windows 11.** Everything works: install with `py -m venv .venv`. There's no
shell sandbox; see [Security](#security). Commands run through `cmd.exe`.

**Immutable Linux (Fedora Atomic, Bazzite) or "no system packages".** Use
`pipx`/`uv tool install`, a venv in your home folder, or the container:

```
podman build -t hive -f Containerfile .
podman run --rm -it --network=host -v ~/.config/hive:/config:Z -v ~/.local/share/hive:/data:Z hive init
podman run --rm -it --network=host -v ~/.config/hive:/config:Z -v ~/.local/share/hive:/data:Z hive serve
```

`--network=host` lets the container reach Ollama on localhost. To work in one
of your folders, mount it (`-v ~/code/x:/work:Z`) and pass
`--workspace /work`.

## Notifications

A long run on a Pi can take a while. Hive can run a command or POST to a URL
when a run finishes, needs an approval, or needs your attention. Configure
it in `hive.yaml`:

```yaml
notify:
  events: [finished, approval, attention]
  command: notify-send Hive "{message}"            # or: espeak "{message}"; CerberOS: /usr/local/lib/cerberos/apps/piper/say.sh "{message}"
  url: http://192.168.1.10:8123/api/webhook/hive   # JSON: message, run, status, kind, goal
```

The command runs **without a shell**. `{message}`, `{run}`, `{status}`,
`{kind}` and `{goal}` are filled in argument by argument. Messages can quote
model output (an approval request includes the command an agent wants to
run), and with no shell that text can't inject commands. A failing hook is
logged and never affects the run.

## Persistence, replay and checkpoints

- **Every event is written before it's shown.** If Hive is killed, the run
  shows as `interrupted`. `hive resume RUN` (or **Resume** in the UI)
  continues from the last checkpoint.
- **Checkpoints** are taken at the start, after each finished task and each
  pipeline stage, before a debate is judged, and whenever you click
  **Checkpoint**. Each one holds the
  task board, the blackboard, every agent's history and summary, the mode's
  state, and the file versions.
- **Rolling back** restores all of that and carries on. Later events stay in
  the log, struck through. Tokens already spent aren't refunded. After a
  resume, the time budget starts again.
- **Replay** re-renders the log; it doesn't call the models again.

## Development

```
pip install -e ".[dev]"
python -m pytest -q          # ~190 tests, ~15 s, no network or model needed
```

The tests include:
- the scheduler (limits, priority and affinity, retries, timeouts, failover)
- the Ollama and OpenAI adapters, against recorded wire formats
- the loop guard
- the workspace sandbox and shell sandbox (including bubblewrap, when
  present)
- file versioning, the tool protocol and context fitting
- the web API and its security checks
- persistence and resume
- an end-to-end pipeline, debate and manager run on the fake model, with
  both native tools and the text protocol

CI runs them on Linux x86-64, Linux ARM64 and Windows, on Python 3.11 and
3.13 ([.github/workflows/hive.yml](../../.github/workflows/hive.yml)).

```
hive/
  backends/   ModelBackend interface; ollama, openai_compat, mock, fake (the rule-based model)
  scheduler.py, context.py, toolproto.py
  agents/     agent worker, role loader      roles/  built-in role templates
  runtime/    app, run, events, board (tasks + blackboard), files, workspace, guard, approvals
  modes/      pipeline, debate, manager, solo
  tools/      @tool, built-ins, shell, web, plugin loader
  store/      SQLite schema and access       web/    FastAPI server and the static UI
  cli.py, cli_runs.py, export.py
```

## What's not done

- **Not yet run against real model servers.** The Ollama and OpenAI-compatible
  adapters are tested against recorded wire formats, and whole runs against
  the fake model. How well real 1–8B models use the tools and follow the
  modes still needs trying on real hardware, and some prompts will need
  tuning.
- **Windows and ARM64 are covered by CI only.** Development happened on
  Linux x86-64.
- **Capability probing is Ollama-only.** For OpenAI-compatible servers, set
  `context` and `tools` yourself.
- **Some numbers are estimates.** Token counts are estimated when a server
  doesn't report them. Tokens/sec isn't shown for very short replies.
- **Interrupt depends on the server.** It cancels the HTTP request; some
  servers finish generating anyway.
- **No users or roles in the UI.** It has a single shared token, and you can't
  delete runs or projects from it (delete `hive.db` or the workspace folder).
- **Stretch goals not built:** agent long-term memory, small-to-big model
  routing (beyond fallbacks for a backend that's down), and MCP client
  support. Notification hooks are built.
