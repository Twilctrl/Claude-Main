// Run state as a fold over the event log. The live view and replay both use
// apply(): live applies events as they stream in, replay re-applies a prefix.

export const FEED_LIMIT = 3000;

export function initState() {
  return {
    run: { status: "created" },
    agents: {},
    order: [],
    tasks: {},
    board: [],
    files: {},
    feed: [],
    approvals: {},
    checkpoints: [],
    rolledBack: [],
    tokens: 0,
    turns: 0,
    lastSeq: 0,
    firstTs: null,
    lastTs: null,
  };
}

const clip = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");

function feed(s, e, item) {
  s.feed.push({ seq: e.seq, ts: e.ts, agent: e.agent, rolledBack: !!e.rolled_back, ...item });
  if (s.feed.length > FEED_LIMIT) s.feed.splice(0, s.feed.length - FEED_LIMIT);
}

function lastToolItem(s, agent, tool) {
  for (let i = s.feed.length - 1; i >= Math.max(0, s.feed.length - 60); i--) {
    const it = s.feed[i];
    if (it.type === "tool" && it.agent === agent && it.tool === tool && it.result === undefined) return it;
  }
  return null;
}

export function apply(s, e) {
  const p = e.payload || {};
  const a = e.agent;
  const ag = a ? s.agents[a] : null;
  if (e.seq) s.lastSeq = Math.max(s.lastSeq, e.seq);
  if (e.ts && e.seq) {
    if (s.firstTs === null) s.firstTs = e.ts;
    s.lastTs = e.ts;
  }
  switch (e.kind) {
    case "run.started":
      Object.assign(s.run, { goal: p.goal, mode: p.mode, project: p.project, workspace: p.workspace, limits: p.limits, started: e.ts });
      for (const m of p.team) {
        if (!s.agents[m.name]) s.order.push(m.name);
        s.agents[m.name] = { ...m, status: "idle", detail: "", turns: 0, tokens: 0, stream: "", reply: "", trigger: "" };
      }
      feed(s, e, { type: "sys", icon: "▶", text: `Run started in ${p.mode} mode` });
      break;
    case "run.status":
      s.run.status = p.status;
      if (p.status === "running") s.run.attention = null;
      if (p.reason) feed(s, e, { type: "sys", icon: "•", text: `Run ${p.status}: ${p.reason}` });
      break;
    case "run.finished":
      Object.assign(s.run, { status: p.status, finished: p, ended: e.ts, attention: null });
      feed(s, e, { type: "sys", icon: p.status === "done" ? "■" : "⚠", cls: p.status === "done" ? "" : "warn", text: `Run ${p.status}${p.reason ? ": " + p.reason : ""}` });
      break;
    case "run.attention":
      s.run.attention = p.message;
      feed(s, e, { type: "sys", icon: "⚠", cls: "warn", text: p.message });
      break;
    case "run.idle":
      feed(s, e, { type: "sys", icon: "…", text: "The team is idle, waiting for you." });
      break;
    case "agent.status":
      if (ag) { ag.status = p.status; ag.detail = p.detail || ""; }
      if (p.status === "thinking") s.run.attention = null; // work resumed
      break;
    case "agent.turn":
      if (ag) { ag.turns = p.turn; ag.stream = ""; ag.trigger = p.trigger; }
      s.turns += 1;
      feed(s, e, { type: "turn", icon: "↳", text: p.trigger, turn: p.turn });
      break;
    case "agent.delta":
      if (ag) { ag.stream += p.text; if (ag.stream.length > 6000) ag.stream = ag.stream.slice(-6000); }
      break;
    case "agent.stream_reset":
      if (ag) ag.stream = "";
      break;
    case "agent.reply":
      if (ag) { ag.reply = p.text; ag.stream = ""; }
      feed(s, e, { type: "reply", icon: "💬", text: p.text });
      break;
    case "agent.updated":
      if (ag) Object.assign(ag, Object.fromEntries(Object.entries(p.changes || {}).filter(([k]) => ["model", "backend"].includes(k))));
      feed(s, e, { type: "sys", icon: "✏", text: `${a}'s ${Object.keys(p.changes || {}).join(", ")} changed` });
      break;
    case "agent.error":
      feed(s, e, { type: "sys", icon: "✗", cls: "bad", text: p.error });
      break;
    case "agent.protocol":
      feed(s, e, { type: "sys", icon: "⌘", cls: "warn", text: `Reply format: ${p.detail}` });
      break;
    case "agent.context":
      feed(s, e, { type: "sys", icon: "⇣", text: `Context ${p.how}: ${p.dropped} older messages folded away` });
      break;
    case "agent.interrupted":
      feed(s, e, { type: "sys", icon: "⏹", text: "Turn interrupted" });
      break;
    case "model.call":
      if (p.ok) {
        const t = (p.prompt_tokens || 0) + (p.completion_tokens || 0);
        s.tokens += t;
        if (ag) { ag.tokens += t; ag.last = p; }
      }
      break;
    case "model.rerouted":
      feed(s, e, { type: "sys", icon: "⤳", cls: "warn", text: `Rerouted ${p.from} → ${p.to}: ${p.reason}` });
      break;
    case "model.waiting_backend":
      feed(s, e, { type: "sys", icon: "⏳", cls: "warn", text: `Waiting for ${p.backends.join(", ")} to come back` });
      break;
    case "model.retry":
      feed(s, e, { type: "sys", icon: "↻", text: `Retry ${p.attempt} on ${p.backend}: ${clip(p.error, 160)}` });
      break;
    case "tool.call":
      feed(s, e, { type: "tool", icon: "→", tool: p.tool, args: p.args });
      break;
    case "tool.result": {
      const it = lastToolItem(s, a, p.tool);
      if (it) { it.result = p.output; it.ok = p.ok; }
      else feed(s, e, { type: "tool", icon: p.ok ? "←" : "✗", tool: p.tool, result: p.output, ok: p.ok, cls: p.ok ? "" : "bad" });
      break;
    }
    case "message":
      feed(s, e, { type: "msg", icon: "✉", from: p.sender, to: p.recipient, text: p.content });
      break;
    case "task.updated": {
      const t = p.task;
      s.tasks[t.id] = t;
      if (["created", "done", "failed"].includes(p.change) || p.change.startsWith("assigned") || p.change.startsWith("handed")) {
        feed(s, e, { type: "task", icon: p.change === "done" ? "✓" : p.change === "failed" ? "✗" : "▢", text: `#${t.id} ${p.change}: ${t.title}`, cls: p.change === "failed" ? "bad" : "" });
      }
      break;
    }
    case "board.entry":
      s.board.push(p.entry);
      feed(s, e, { type: "board", icon: "📌", text: `${p.entry.kind}: ${p.entry.title}` });
      break;
    case "file.version":
      (s.files[p.path] = s.files[p.path] || []).push({ ...p, agent: a, seq: e.seq });
      feed(s, e, { type: "file", icon: "✎", path: p.path, version: p.version, text: `${p.path} ${p.deleted ? "deleted" : p.created ? "created" : "changed"} (v${p.version}, +${p.added} −${p.removed})` });
      break;
    case "approval.requested":
      s.approvals[p.id] = { ...p, agent: a, status: "pending", seq: e.seq };
      feed(s, e, { type: "sys", icon: "⚠", cls: "warn", text: `Wants approval for ${p.tool}: ${clip(JSON.stringify(p.args), 200)}` });
      break;
    case "approval.decided":
      if (p.id != null && s.approvals[p.id]) s.approvals[p.id].status = p.approved ? "approved" : "rejected";
      feed(s, e, { type: "sys", icon: p.approved ? "✓" : "✗", text: `${p.tool || "Request"} ${p.approved ? "approved" : "rejected"} by ${p.by}${p.reason ? ": " + p.reason : ""}` });
      break;
    case "guard.trigger":
      feed(s, e, { type: "sys", icon: "🛡", cls: "warn", text: `Loop guard (${p.rule}): ${p.action}, ${p.detail}` });
      break;
    case "guard.blocked":
      feed(s, e, { type: "sys", icon: "🛡", cls: "warn", text: `Blocked: ${p.reason}` });
      break;
    case "checkpoint.created":
      s.checkpoints.push({ id: p.id, label: p.label, seq: e.seq, ts: e.ts });
      break;
    case "run.resumed":
    case "checkpoint.restored":
      if (e.kind === "run.resumed") { s.run.finished = null; s.run.ended = null; s.run.status = "running"; }
      s.tasks = Object.fromEntries((p.tasks || []).map((t) => [t.id, t]));
      s.board = (p.board || []).slice();
      s.rolledBack.push([p.seq, e.seq]);
      for (const it of s.feed) if (it.seq > p.seq && it.seq < e.seq) it.rolledBack = true;
      feed(s, e, { type: "sys", icon: "⏪", cls: "warn", text: e.kind === "run.resumed" ? `Resumed from checkpoint ${p.id} (${p.label})` : `Rolled back to checkpoint ${p.id} (${p.label})` });
      break;
    case "mode.stage":
      feed(s, e, { type: "sys", icon: "▶", text: `Stage: ${a} (${p.kind}${p.round > 1 ? ", round " + p.round : ""})` });
      break;
    case "mode.phase":
      feed(s, e, { type: "sys", icon: "▶", text: `Debate: ${p.phase}${p.round ? " (round " + p.round + ")" : ""}` });
      break;
    case "mode.verdict":
      feed(s, e, { type: "sys", icon: p.approved ? "✅" : "↩", cls: p.approved ? "" : "warn", text: `${p.approved ? "Approved" : "Changes requested"} (round ${p.round})` });
      break;
    case "mode.fallback":
      feed(s, e, { type: "sys", icon: "⤳", cls: "warn", text: p.detail });
      break;
    case "mode.error":
      feed(s, e, { type: "sys", icon: "✗", cls: "bad", text: `Mode error: ${p.error}` });
      break;
    case "memory.recalled":
      feed(s, e, { type: "memory", icon: "🧠", text: `Recalled ${p.notes.length} note${p.notes.length === 1 ? "" : "s"} from earlier runs`, notes: p.notes });
      break;
    case "memory.saved":
      if (p.created) feed(s, e, { type: "sys", icon: "🧠", text: `${p.kind === "lesson" ? "Lesson for next time" : "Remembered"}: ${p.text}` });
      break;
    case "memory.reflecting":
      feed(s, e, { type: "sys", icon: "🧠", text: `Writing lessons for next time: ${p.agents.join(", ")}` });
      break;
    case "memory.forgotten":
      feed(s, e, { type: "sys", icon: "🧠", cls: "warn", text: `Forgot ${p.count} note${p.count === 1 ? "" : "s"} (${p.reason})` });
      break;
    case "backend.down":
    case "backend.up":
      feed(s, e, { type: "sys", icon: e.kind === "backend.up" ? "●" : "○", cls: e.kind === "backend.up" ? "" : "bad", text: `Backend ${p.backend} is ${e.kind.split(".")[1]}${p.reason ? ": " + clip(p.reason, 160) : ""}` });
      break;
    default:
      break;
  }
  return s;
}

export function reduceAll(events, upto = events.length) {
  const s = initState();
  for (let i = 0; i < upto; i++) apply(s, events[i]);
  return s;
}

export function pendingApprovals(s) {
  return Object.values(s.approvals).filter((x) => x.status === "pending");
}
