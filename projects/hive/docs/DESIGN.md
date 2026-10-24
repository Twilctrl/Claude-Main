# Hive: design

Status: **implemented** (M1–M5). Written before the code; §19 lists where
the build differed. Hive lives in `projects/hive/`.
CerberOS (`projects/cerberos`) is the reference small-board setup: a Pi 5 whose
hailo-ollama NPU head, Ollama CPU head and gate all work as Hive backends.

## 1. What Hive is

Hive is one Python process that runs a team of LLM agents against model servers
you already have, on this machine or elsewhere on your LAN, and lets them work
together on a project directory. It makes no cloud calls. The only network
traffic goes to the backends you configure, plus the optional web-fetch tool.

Non-goals:

- Several Hive processes sharing one run. One process orchestrates, and the
  models it calls can be anywhere.
- Managing models. Pulling and deleting stay with Ollama or `cerb`.
- Being a security boundary for hostile code. See §10.

## 2. Architecture

```
             ┌─────────────── hive process (one asyncio loop) ────────────────┐
  browser ──▶│  FastAPI: REST for actions, SSE for live events                 │
  hive CLI ─▶│                                                                 │
             │  ┌─────────────── Run (one per active run) ──────────────────┐  │
             │  │  Mode (conductor): pipeline | debate | manager             │  │
             │  │    assigns tasks, sends instructions, decides "done"       │  │
             │  │  Agent workers: [planner] [coder] [reviewer] [tester] ...  │  │
             │  │    inbox → context builder → Scheduler → tool executor     │  │
             │  │  Shared: Workspace · Blackboard · TaskBoard · MessageBus   │  │
             │  │  LoopGuard · Approvals · Checkpoints                       │  │
             │  └───────────────────────────┬────────────────────────────────┘  │
             │                every change  ▼                                   │
             │  EventLog (SQLite, append-only) ──▶ EventBus ──▶ SSE, CLI, guard │
             │                                                                  │
             │  Scheduler: a queue per backend, concurrency slots, health      │
             └──────┬──────────────────────┬──────────────────────┬─────────────┘
                    ▼                      ▼                      ▼
             Ollama :11434          hailo-ollama :8000      llama.cpp, LM Studio,
             (desktop GPU)          (Pi 5 NPU)              vLLM (OpenAI /v1)
```

| Package | Responsibility |
| --- | --- |
| `hive.backends` | `ModelBackend` interface, adapters (Ollama, OpenAI-compatible, mock), registry |
| `hive.scheduler` | Per-backend queues and slots, timeouts, retries, health, rerouting, load metrics |
| `hive.context` | Token estimates, per-agent budgets, summarising and truncating history |
| `hive.toolproto` | Native tool calls, and the JSON-in-text fallback with validation and re-prompts |
| `hive.agents` | Agent definitions, role templates, the agent worker loop |
| `hive.runtime` | Run lifecycle, message bus, blackboard, task board, loop guard, approvals, checkpoints |
| `hive.modes` | Pipeline, Debate, Manager/workers |
| `hive.tools` | Built-in tools, permissions, workspace sandbox, shell, web fetch, plugin loader |
| `hive.store` | SQLite schema and queries, the event log |
| `hive.web` | FastAPI app, SSE stream, static UI |
| `hive.cli` | The `hive` command |

**The event log is the spine.** Every state change is appended to `events`
first, then fanned out in-process: messages, tool calls, task updates, file
writes, status changes, approvals and model-call metrics. The UI, the CLI
printer, the loop guard and the modes are all subscribers. Replay, transcripts
and the diff view are built from the same rows.

The process is single-threaded asyncio. SQLite runs in WAL mode behind one
writer on a worker thread, so a slow SD card on the Pi doesn't stall the loop.

## 3. Model backends

```python
@dataclass
class Capabilities:
    max_context: int      # tokens the server will actually honour
    tool_calling: bool    # native tools in the chat API
    streaming: bool
    concurrency: int      # simultaneous requests the device can take
    speed: float          # relative; 1.0 = reference. Used for ETAs and routing

class ModelBackend(ABC):
    name: str
    capabilities: Capabilities               # backend default
    def capabilities_for(self, model: str) -> Capabilities   # with per-model overrides
    async def chat(self, messages, tools=None, stream=False, *, model=None,
                   params=None) -> AsyncIterator[ChatEvent]
        # yields TextDelta events (only when stream=True), then exactly one ChatResult
        # (text, tool_calls, usage, timings)
    async def list_models(self) -> list[ModelInfo]
    async def health(self) -> Health          # up/down, latency, loaded model if known
```

`chat()` always returns an async iterator, so streaming and non-streaming
callers share one code path. Without streaming it yields only the result.

Adapters:

- **`ollama`**: `/api/chat` with NDJSON streaming and native tools. Always sends
  `options.num_ctx`, because Ollama otherwise truncates silently to its default
  window. Reads `eval_count` and `eval_duration` for exact tokens/sec. Probes
  `/api/show` for `capabilities` and `context_length`. hailo-ollama speaks a
  subset (no tools, no `/api/show`, one request at a time), so it's handled with
  config flags (`tools: false`, `probe: false`) rather than its own adapter.
  The CerberOS gate works through this adapter too.
- **`openai`**: `/v1/chat/completions` with SSE streaming, native tools, an
  optional bearer key, and `/v1/models`. Covers llama.cpp server, LM Studio and
  vLLM.
- **`mock`**: no network.
  - `echo` repeats the prompt back.
  - `scripted` plays responses and tool calls from a YAML script keyed by
    role and turn.
  - `fake` is a small rule-based "model" that plays each built-in role well
    enough to drive a whole run.

  Latency, tokens/sec and failures (timeouts, 500s, going down at call N) are
  all configurable. Tests, demos and UI work use it.
- **Adding one**: subclass `ModelBackend` and decorate it with
  `@register_backend("name")`. Or, for code outside Hive, set
  `type: mypkg.module:Class` in YAML.

`backends.yaml` (full single-machine and LAN examples go in the README):

```yaml
backends:
  - name: desktop
    type: ollama
    url: http://192.168.1.50:11434
    default_model: qwen2.5-coder:14b
    concurrency: 2              # match OLLAMA_NUM_PARALLEL
    context: 16384
    device: desktop-gpu         # optional: backends on one device share its limit
    models:                     # optional per-model overrides
      llama3.2:3b: {context: 8192}
  - name: pi-npu
    type: ollama                # hailo-ollama
    url: http://pi5.local:11434 # CerberOS gate; the model name picks the head
    default_model: llama3.2:1b@npu
    concurrency: 1
    context: 2048
    tools: false
    speed: 0.3
    fallbacks: [pi-cpu]
    timeouts: {first_token: 180, total: 900}
```

A backend entry is a logical device. Two entries may share a URL. That's how
one CerberOS gate becomes two queues, `pi-npu` and `pi-cpu`, each with its own
limit.

## 4. Scheduler

- **Queues.** Each backend has one queue, and each `device` has one too. A
  request needs a backend slot and a device slot. Queues are FIFO, with a
  priority bump for interactive calls (`hive chat`, a user's message to an
  agent). An agent never has more than one model call outstanding, so FIFO is
  fair between agents.
- **Model affinity.** A server that holds one model at a time reloads on every
  model switch, which takes tens of seconds. CerberOS's CPU head is set up that
  way. So the scheduler prefers the next queued request for the model it served
  last. A request can be skipped this way at most N times, so nothing starves.
- **Timeouts.** Connect, first token, idle between tokens, and total. The
  first-token timeout is generous because it covers a cold model load.
- **Retries.** Exponential backoff with jitter on connection errors, timeouts,
  429 and 5xx. No retry on any other 4xx. Default: 2 retries.
- **Health.** A backend is `up`, `degraded` or `down`. Three consecutive
  failures, or a failed probe, mark it down. A background probe (`health()`
  every 10 s, backing off to 60 s) brings it back.
- **Backend down.** Agents with `fallbacks` reroute to the first healthy one.
  The reroute is logged as an event and shown on the agent's card. Agents with
  no healthy fallback wait in status `waiting_backend` and resume when the
  backend returns. If it stays down past `backend_down_timeout`, the run pauses
  and asks you what to do.
- **Metrics.** Published per backend every second: queue depth, requests in
  flight, OK and failed counts, p50 latency, tokens/sec (a moving average of
  generation speed), loaded model, and health. The load panel shows these.

## 5. Context management

- **Budget.** An agent's budget is the smaller of its `context_budget` and the
  model's `max_context`, minus the tokens reserved for the reply (`max_tokens`,
  default 512).
- **Token counts** are estimates: characters ÷ 3.5 by default, configurable per
  backend. After each call, the prompt-token count the server reports corrects
  the estimate. Nothing downloads a tokenizer.
- **Prompt assembly** fills the budget in this order:
  1. system prompt and role
  2. tool list (fallback protocol only)
  3. pinned facts: the goal, the current task, and its dependencies' results
  4. "since your last turn": a digest of channel messages
  5. rolling summary
  6. recent turns, newest first, until the budget is full
- **When history no longer fits,** the oldest turns not yet summarised are
  folded into the rolling summary. The agent's own backend writes it, or a
  `summarizer` backend if one is configured. The summary is capped at about 15%
  of the budget. Truncation takes over if summarising fails, or if the budget
  is under 2k tokens: drop the oldest turns first, then cut long tool outputs
  down to their head and tail.
- **Tool outputs** are clipped before they enter history. File reads are paged:
  `read_file(path, start_line, max_lines)`.

## 6. Tool calls and the fallback protocol

If the model supports native tools, schemas go in the API request and tool
calls come back structured. If not, the system prompt gets a compact tool list
and this contract, one action per reply:

```
To use a tool, reply with only:  {"tool": "write_file", "args": {"path": "...", "content": "..."}}
When you are done, reply with:   {"final": "your answer"}
```

- **Parsing is forgiving.** It accepts fenced or bare JSON, takes the first
  balanced object, tolerates trailing commas, and drops unknown keys.
- **Validation.** Arguments are checked against the tool's schema: pydantic
  models generated from type hints. Native calls that fail validation get the
  same handling as fallback replies.
- **Errors.** A parse or validation failure gets one short correction: the
  error plus a valid example. After `max_reprompts` (default 2), the reply is
  treated as a plain-text final answer and logged as a protocol failure, which
  the loop guard counts.

## 7. Agents and roles

```yaml
# roles/coder.yaml. Built in; a file with the same name in the config dir overrides it.
name: coder
description: Implements tasks by writing and editing files in the workspace.
system_prompt: |
  You are the Coder on a small team. ...
tools: [list_files, read_file, write_file, edit_file, list_tasks, complete_task, send_message]
params: {temperature: 0.2}
context_budget: 8192
```

An agent is a role template plus the run's settings for it: `name`, `backend`,
`model`, `fallbacks`, and any overrides of prompt, params, tools or budget.

Built-in roles: Planner, Coder, Reviewer, Researcher, Writer, Tester and
Coordinator, plus Judge for debate mode. The prompts are short and imperative
because that's what 1–3B models follow best.

Each agent is its own asyncio task with its own inbox and state:

```
loop:
  wait until the inbox has items, the agent isn't paused, and the loop guard allows a turn
  take the inbox items: task assignment, direct message, user message, mode instruction
  turn:
    build context → scheduler.chat (tokens stream to the bus)
    tool call?  → permission check → approval if risky → execute → add result → call again
                  (at most max_tool_steps times)
    final text? → deliver it as the mode says (reply to the sender, or set the task result)
status: idle | thinking | waiting_backend | waiting_approval | paused | stopped
```

Agents change shared state only through tools, so every effect is permission-
checked and logged.

## 8. Collaboration

**Workspace.** A directory on disk, `<data>/workspaces/<slug>` by default or
any directory you choose. Hive keeps its own state in SQLite, not in the
workspace, so agents can't edit it.

**Blackboard.** Typed entries:

- `note`
- `decision`
- `artifact`: a file reference or inline content
- `deliverable`

The tools are `post_note`, `record_decision` and `read_board(kind?, query?)`.

**Task board.** A task has an id, title, description, owner, status, a
`depends_on` list, a result, and its creator. Status is one of `todo`,
`in_progress`, `blocked`, `review`, `done` or `failed`. A task is *ready* once
all its dependencies are done.

- Planner and Coordinator only: `create_task`, `assign_task`
- Everyone: `claim_task`, `complete_task(result)`, `fail_task(reason)`,
  `handoff_task(to, note)`, `list_tasks`

**Messaging.** `send_message(to, text)` sends a direct message, and
`broadcast(text)` posts to the project channel. These wake an agent: direct
messages, task assignments, user messages and mode instructions. Broadcasts
don't; they go into the "since your last turn" digest at the top of the
agent's next prompt instead. That stops broadcast storms. `wake_on_broadcast`
can be turned on per agent.

**Modes** are small async classes that react to events. They drive agents
through the same inboxes and tasks the agents use with each other.

| Mode | Flow | Ends when |
| --- | --- | --- |
| Pipeline | Stages run in order (default planner → coder → reviewer → tester). Each stage gets the goal, the previous stage's output, and the board. Reviewer and tester answer with `approve(notes)` or `request_changes(notes)`. Changes go back to the coder, up to `max_rounds` | The tester approves. If rounds run out, the run ends as `needs_attention` |
| Debate | N proposers answer in parallel, each critiques the others, each revises, then a judge picks one or merges them | The judge posts the deliverable |
| Manager/workers | The coordinator breaks the goal into tasks with dependencies. Idle workers claim ready tasks that match their role, in parallel. The coordinator reviews results, adds follow-ups, and calls `finish_run(summary)` | `finish_run`, or all tasks done and the coordinator confirms |

Every mode ends by writing a `deliverable` entry to the blackboard: a summary
and the list of files. It heads the exported transcript.

**Shared files: optimistic versioning.**

- **Versions.** Every write creates a version row: agent, time, content hash.
- **Stale writes.** The file tools remember which version each agent last read,
  so small models never handle version numbers. `write_file` to an existing
  file is rejected if the file changed after this agent read it, or if the
  agent never read it. The error includes the diff so the agent can redo its
  change.
- **Edits.** `edit_file(path, find, replace)` applies whenever `find` still
  matches, so most concurrent edits merge on their own.
- **Atomicity.** Writes go to a temp file and are renamed into place, under a
  per-path lock.
- **Outside edits.** Hive detects edits made outside it by content hash and
  records them with author `external`.
- **Diffs.** The UI shows every version as a diff, filterable by agent.

## 9. Loop protection

`LoopGuard` subscribes to the event log. Everything below is configurable in
`hive.yaml` and per run.

| Limit | Default |
| --- | --- |
| Turns per agent / per run | 30 / 150 |
| Tool steps in one turn | 8 |
| Tokens per run (prompt + completion) | 400k |
| Wall-clock time per run | 2 h |
| Repeats: near-identical replies (similarity ≥ 0.9) or identical tool calls in a row | 3 |
| Stall: turns with no progress (no task status change, file change or new board entry) | 12 |
| Ping-pong: messages between the same two agents with no progress | 6 |
| Fallback-protocol failures in a row | 4 |

A soft limit (repeats, stall, ping-pong, protocol failures) first gets a nudge,
a system note like "You are repeating yourself; try something different or
finish with a summary." If that doesn't help, the agent is paused and you're
asked. A hard budget (tokens, wall-clock, run turns) stops the run as
`budget_exhausted` after taking a checkpoint, so you can raise the budget and
resume.

## 10. Tools, permissions and sandboxing

Each agent has an allow-list of tools. A tool not on it is never offered to the
model and can't be executed. Risky tools follow the agent's approval policy:
`ask` (default), `allow` or `deny`.

| Tools | Default | Notes |
| --- | --- | --- |
| Files: list, read, write, edit, delete | All on; delete asks first | Workspace only (below) |
| Board, tasks, messages | On | |
| Shell | **Off.** Opt-in per agent; asks before every command unless allow-listed | Below |
| Web fetch | **Off** | GET only, size and time limits, optional host allow-list, HTML reduced to text |
| Plugins | Off until listed in an agent's tools | Below |

**Path sandbox.** Paths are relative to the workspace. A path is rejected if it:

- is absolute
- has a drive letter or UNC prefix
- uses a Windows reserved name (CON, NUL and so on)
- resolves, with symlinks followed, to anywhere outside the workspace root

The check runs when the file is opened. Reads and writes have size limits.

**Shell.**

- **Process.** Commands run as an asyncio subprocess in their own process
  group. The working directory is the workspace, stdin is closed, and the
  environment is scrubbed: no tokens, and `HOME` points to a scratch directory.
- **Limits.** The timeout defaults to 60 s, then the whole process tree is
  killed (`killpg` on POSIX, `taskkill /T` on Windows). Output is capped at
  64 KB, keeping the head and tail.
- **bubblewrap.** On Linux, commands run inside `bwrap` if it's installed (it
  ships with Fedora Atomic; on Pi OS, `apt install bubblewrap`): read-only
  root, writable workspace, no network.
- **Without bwrap this is not a security boundary.** A command can read your
  files and reach the network. That's why shell is off by default and asks
  before every command. The README's security section says so plainly.

**Plugins.** Drop a Python file into `<config dir>/tools/` with functions
decorated `@hive.tool(risky=...)`. The schema comes from the type hints and
the docstring.

- **Config dir only.** Plugins never load from a project workspace: agents can
  write there, so that would let an agent create its own unsandboxed tool.
- **Full privileges.** Plugin code runs in-process with your permissions.

**Approvals.**

- **Request.** A risky call creates an approval request (an event, a UI modal
  and a CLI prompt), and the agent waits in `waiting_approval`.
- **Reject.** The agent gets the refusal and your reason.
- **Timeout.** Unanswered requests are rejected after a timeout (default
  15 min; 0 waits forever).
- **Headless runs.** `--approve ask|all|none`.

## 11. Data model (SQLite)

```
projects      id, slug, name, goal, workspace, created_at, settings
runs          id, project_id, mode, status, config (team + limits snapshot),
              started_at, ended_at, stats
agents        id, run_id, name, role, backend, model, fallbacks, system_prompt,
              params, tools, context_budget, status
events        id, run_id, seq, ts, kind, agent, payload (JSON)    -- append-only, authoritative
messages      id, run_id, seq, sender, recipient (agent | #channel | user), content, reply_to
tasks         id, run_id, title, description, owner, status, depends_on, result,
              created_by, updated_at
board         id, run_id, kind, title, content, author, seq
file_versions id, project_id, run_id, path, version, agent, seq, blob, size, deleted
blobs         hash, data (zlib)                                   -- content-addressed
model_calls   id, run_id, agent, backend, model, prompt_tokens, completion_tokens,
              latency_ms, ok, error, request, response
approvals     id, run_id, agent, tool, args, status, decided_by, reason, created_at, decided_at
checkpoints   id, run_id, seq, label, state (JSON: tasks, board, agent histories and
              summaries, path → file version)
```

- **Authority.** `messages`, `tasks` and `board` are kept up to date from
  events for fast queries. `events` is authoritative.
- **Token deltas** reach live subscribers only. The finished message is stored.
- **Scope.** A project's workspace persists across its runs. Each run has its
  own agents, tasks and board.

## 12. Message flow: one pipeline run

```
$ hive run "add a --json flag to report.py" --mode pipeline
 1  run created, agents spawned, checkpoint #0
 2  pipeline creates T1 "Plan", assigns it to planner        events: task.created, task.assigned
 3  planner wakes → context → scheduler (desktop queue) → backend → reply: create_task ×3
      each call: permission ✓ → execute → events tool.call, tool.result
 4  planner: complete_task(T1, plan)                          event task.done → checkpoint #1
 5  pipeline assigns T2..T4 to coder in dependency order
 6  coder: read_file, edit_file ...                           event file.version (diff in UI)
 7  pipeline → reviewer: "review changes since checkpoint #1" (the diff is in its prompt)
 8  reviewer: request_changes("handle empty input") → back to coder (round 2 of 3)
 9  reviewer: approve → tester: run_shell("pytest -q") → approval request → you approve
10  tester: approve → deliverable written → run done → transcript exportable
    (the loop guard and the budget meters are live throughout)
```

## 13. User interfaces

**Web UI** (`hive serve`, default http://127.0.0.1:8765):

- **Projects:** the list, and New project: goal, mode, team (roles, plus
  backend and model per agent), limits, Run.
- **Run view panels:**
  - agent cards: status, backend, live output, pause/resume, edit prompt
  - task board, in columns by status
  - message feed, with a box for posting as you to any agent or the channel
  - file tree with per-version diffs by agent
  - backend load: queue depth, in flight, tokens/sec, health
  - approval queue
  - checkpoints, each with "roll back to here"
  - budget meters
- **Replay:** any past run, with a timeline scrubber over the same views.
- **Transport:** REST for actions, plus one SSE stream per run
  (`/api/runs/{id}/events?after=seq`). After a dropped connection, the stream
  resumes from the last seq.
- **Binding:** localhost by default. `--host 0.0.0.0` requires a token,
  generated and printed once, because the UI can approve shell commands.

**CLI:**

```
hive init                          # write config, probe for Ollama on localhost, pick a model
hive backends [--check]            # list backends, health, models
hive chat [--backend B] [--model M] [--role R]     # single-agent chat (M1)
hive run "goal" [--mode pipeline|debate|manager] [--team planner,coder,...]
                [--workspace DIR] [--approve ask|all|none]
hive serve [--host H] [--port P]
hive runs | hive show RUN | hive replay RUN | hive export RUN -o run.md
hive pause|resume|stop RUN [--agent NAME]          # talks to a running server
hive roles [show NAME]
```

## 14. Persistence, replay, checkpoints

- **Crash safety.** Everything is written as it happens, so a crash loses at
  most the model call in flight. After a restart, a run that was `running`
  shows as `interrupted` and can resume from its last checkpoint.
- **Replay** re-renders the event log: the UI's timeline, or `hive replay` in a
  terminal with speed control. It doesn't call any model.
- **Checkpoints** are taken at run start, after each task or stage, and before
  risky actions. You can also take one from the UI. A checkpoint stores the
  task board, the blackboard, each agent's history and summary, and the map of
  path to file version (file contents are already in `blobs`).
- **Rollback** pauses the run and restores the files and that state. Later
  events are marked rolled back but kept (greyed out in the UI), so nothing is
  lost. Then the run resumes.
- **Export** writes a Markdown transcript: goal, team and deliverable first,
  then the timeline of messages, tool calls (collapsed), decisions and diffs.

## 15. Configuration, paths, installation

- **Config dir:** `$HIVE_CONFIG_DIR` if set. Otherwise `~/.config/hive` on
  Linux, `%APPDATA%\hive` on Windows, `~/Library/Application Support/hive` on
  macOS.
- **Data dir:** `$HIVE_DATA_DIR` if set. Otherwise `~/.local/share/hive` on
  Linux and `%LOCALAPPDATA%\hive` on Windows. It holds `hive.db` and the
  default workspaces. Hive writes nowhere except these two dirs and the
  workspaces you choose.
- **Config files:** `backends.yaml`, `hive.yaml` (limits, defaults, UI),
  `roles/*.yaml` overrides, `tools/*.py` plugins.
- **Install:** `pipx install ./projects/hive`, `uv tool install`, or a plain
  venv; none of them touch system packages. For immutable distros there's a
  `Containerfile` for podman, with the config and data dirs as volumes and host
  networking so it can reach Ollama on localhost.
- **Dependencies:** fastapi, uvicorn, httpx, pydantic and pyyaml. All are pure
  Python or have wheels for x86-64, aarch64 and Windows. The UI's two small JS
  libraries are vendored, so nothing is fetched at runtime.

## 16. Key risks

| Risk | Mitigation |
| --- | --- |
| 1–3B models (the Pi's tier) are unreliable at tool use and multi-step protocols | One action per reply. A forgiving parser that re-prompts. Small tool sets per role. Modes where the conductor sequences the work, not the model. Put planner, judge and coordinator on the biggest model, and give small models narrow roles |
| Tiny context windows (2k on the NPU) | Budgets, rolling summary, paged reads, clipped tool output, pinned task facts |
| Ollama silently truncating to its default context | Always send `num_ctx` |
| Model-swap thrash on one-model-at-a-time servers | Model affinity in the queue, and a UI warning when one backend serves several models |
| Slow devices make runs long; timeouts fire during cold loads | Separate first-token timeout, per-backend timeouts, ETAs from measured tokens/sec |
| Shell commands escape the "sandbox" | Off by default, approval per command, bwrap when available, documented risks |
| Prompt injection from files or fetched pages | Per-agent tool allow-lists, approval for risky actions, web fetch off by default |
| Agents ping-ponging or stalling | Loop guard (§9); broadcasts don't wake agents |
| Concurrent edits clobbering each other | Optimistic versioning and `edit_file` |
| SQLite stalls on SD cards | WAL, a single writer thread, batched metrics |
| Windows differences (paths, process-tree kill, no bwrap) | Explicit Windows path rules and kill path. I can only run Linux here, so Windows needs CI or your testing |

## 17. Milestones

| | Scope | Demo |
| --- | --- | --- |
| M1 | Backend interface; Ollama, OpenAI and mock adapters; scheduler (queues, limits, affinity, timeouts, retries, health, metrics); config and paths; `hive init`, `hive backends`, `hive chat` | `hive chat --backend mock`, or against your Ollama |
| M2 | Store and event log, agent worker, message bus, blackboard, task board, sandboxed file tools, context manager, fallback tool protocol; `hive run` with a basic conductor | `hive run "..." --backend mock` printing the live event stream |
| M3 | Pipeline, debate and manager modes; loop guard; shell (with bwrap), web fetch, plugins; approvals in the CLI | A run in each mode on the fake model |
| M4 | Web UI: projects, new run, live view, controls, load panel, diffs, approvals | `hive serve` |
| M5 | Checkpoints and rollback, replay, Markdown export, resume after restart, README, Containerfile, the end-to-end test, polish | `hive replay`, `hive export` |

Tests as the brief asks:

- Unit tests for the scheduler, loop guard, path sandbox, and the adapters.
  Adapter tests feed recorded Ollama and OpenAI wire responses through
  `httpx.MockTransport`, with no network.
- An end-to-end pipeline run on the fake model.

The full suite runs at the end of every milestone.

## 18. Changes to the requested stack

- **UI: Preact + htm instead of HTMX or React/Vite.** They're vendored as ES
  modules (about 15 KB) and need no build step. The live view is a dozen panels
  updating from one event stream. That's a component model's job, and HTMX's
  partial swaps fit it poorly. But a Node toolchain is a burden on a Pi, on an
  immutable distro and on an offline install. Preact + htm runs straight from
  the files FastAPI serves, so there's no build to fall out of sync.
- **CLI: argparse instead of Typer or Click,** which keeps the dependency list
  at five.
- **SQLite: stdlib `sqlite3` on a worker thread,** with no ORM or aiosqlite.
  The schema is small and the queries are simple.

Everything else (Python 3.11+, FastAPI, asyncio, SQLite) is as requested.

## 19. What changed while building

The design held. These are the places where the build added or adjusted
something, mostly for small models or found by tests:

- **Reply depth.** Agent-to-agent replies stop waking the recipient at depth
  2: a question, its answer, then a thank-you that's only shown, not acted
  on. Without this, two small models thank each other forever.
- **`waits_for_user` modes.** A mode can declare that an idle team is waiting
  for the user rather than stuck. "Idle" is reported once per quiet spell.
- **Separate `debate_rounds` limit** (default 1), so debates on slow devices
  don't take 11 turns by default.
- **Restores go through the mode's event queue,** like `start`, so idle
  checks wait until the mode has re-issued its work. Each mode has an
  `on_restore` hook, and the tests resume a finished run from every one of
  its checkpoints, in every mode.
- **Checkpoints** are taken after every finished task too, not only per
  stage, so a crash loses at most one task's work.
- **Event payload keys can't collide with `emit()`'s own parameters.**
  `emit(kind, agent, /, **payload)` makes those two positional-only (a field
  called `kind` broke it twice).
- **The run's SSE stream ends cleanly for finished runs.** The client closes
  instead of reconnecting forever.
- **The shell runs commands through `create_subprocess_shell` on Windows,**
  because argv-style quoting mangles `cmd.exe` command lines. Inside
  bubblewrap, `/tmp` is a private tmpfs.
- **Tokens per second needs at least 4 tokens and 50 ms of generation,** so
  pure tool-call replies don't show absurd speeds.
- **Not built:** replaying with re-execution (replay re-renders the log, as
  agreed), and the stretch goals except notification hooks (`hive/notify.py`:
  a command run without a shell, and/or a JSON webhook, when a run finishes or
  needs you).

