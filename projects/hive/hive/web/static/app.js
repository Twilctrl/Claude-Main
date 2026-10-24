// Hive web UI: Preact + htm, no build step. State comes from the run's event stream (state.js).
import { html, render, useState, useEffect, useRef, useMemo, useCallback } from "./vendor/htm-preact-3.1.1.js";
import { initState, apply, reduceAll, pendingApprovals } from "./state.js";

// --- helpers ------------------------------------------------------------------------------

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch("/api" + path, {
    method,
    headers: { "X-Hive": "1", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = text;
  try { data = JSON.parse(text); } catch { /* plain text */ }
  if (!res.ok) throw new Error((data && (data.error || data.detail)) || text || res.statusText);
  return data;
}

let showToast = () => {};
const toast = (msg) => showToast(String(msg));
const act = (fn) => async (...args) => { try { await fn(...args); } catch (e) { toast(e.message || e); } };

function useRoute() {
  const parse = () => (location.hash.replace(/^#\/?/, "") || "").split("/").filter(Boolean);
  const [route, setRoute] = useState(parse());
  useEffect(() => {
    const on = () => setRoute(parse());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return route;
}

function usePoll(fn, ms, deps = []) {
  useEffect(() => {
    let alive = true;
    const tick = async () => { if (alive) { try { await fn(); } catch { /* keep polling */ } } };
    tick();
    const t = setInterval(tick, ms);
    return () => { alive = false; clearInterval(t); };
  }, deps);
}

const pad = (n) => String(n).padStart(2, "0");
const fmtTime = (ts) => { const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
function fmtDur(s) {
  if (s == null) return "–";
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${pad(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${pad(Math.floor((s % 3600) / 60))}m`;
}
const fmtNum = (n) => (n >= 10000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + "k" : String(n ?? 0));
const clip = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");
const FINAL = ["done", "failed", "stopped", "needs_attention", "budget_exhausted", "interrupted"];

const Chip = ({ status, label, nodot }) => html`<span class=${`chip s-${status}${nodot ? " nodot" : ""}`}>${label || String(status || "").replace(/_/g, " ")}</span>`;

const Logo = () => html`<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="var(--accent)" d="M12 2l8.66 5v10L12 22l-8.66-5V7z"/><path fill="var(--bg)" d="M12 7.2l4.16 2.4v4.8L12 16.8l-4.16-2.4V9.6z"/></svg>`;

function Toast() {
  const [msg, setMsg] = useState(null);
  showToast = (m) => { setMsg(m); setTimeout(() => setMsg(null), 6000); };
  return msg ? html`<div class="toast" role="alert" onClick=${() => setMsg(null)}>${msg}</div>` : null;
}

function Modal({ title, onClose, children }) {
  useEffect(() => {
    const k = (e) => e.key === "Escape" && onClose();
    addEventListener("keydown", k);
    return () => removeEventListener("keydown", k);
  }, []);
  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="modal" role="dialog" aria-label=${title}>
      <div class="row"><h2 class="grow">${title}</h2><button class="ghost" onClick=${onClose} aria-label="Close">✕</button></div>
      ${children}
    </div>
  </div>`;
}

// --- app shell ------------------------------------------------------------------------------

function App() {
  const route = useRoute();
  const [info, setInfo] = useState(null);
  useEffect(() => { api("/info").then(setInfo).catch((e) => toast(e.message)); }, []);
  let page;
  if (route[0] === "new") page = html`<${NewRun} info=${info} />`;
  else if (route[0] === "runs" && route[1]) page = html`<${RunView} key=${route[1]} id=${route[1]} info=${info} />`;
  else if (route[0] === "backends") page = html`<${BackendsPage} />`;
  else if (route[0] === "memory") page = html`<${MemoryPage} />`;
  else page = html`<${Home} info=${info} />`;
  const on = (r) => (route[0] || "") === r ? "on" : "";
  return html`
    <header class="top">
      <a class="brand" href="#/"><${Logo} /> Hive</a>
      <nav>
        <a class=${on("")} href="#/">Runs</a>
        <a class=${on("new")} href="#/new">New run</a>
        <a class=${on("backends")} href="#/backends">Backends</a>
        <a class=${on("memory")} href="#/memory">Memory</a>
      </nav>
      <span class="spacer"></span>
      <span class="muted small">${info ? `v${info.version}` : ""}</span>
    </header>
    <main>${page}</main>
    <${Toast} />`;
}

// --- home ---------------------------------------------------------------------------------------

function Home() {
  const [runs, setRuns] = useState(null);
  const [backends, setBackends] = useState([]);
  usePoll(async () => setRuns(await api("/runs?limit=100")), 3000);
  usePoll(async () => setBackends(await api("/backends")), 2000);
  return html`<div class="home-grid">
    <section class="panel">
      <header><h2>Runs</h2><a class="btn primary" href="#/new">New run</a></header>
      ${runs === null ? html`<div class="empty">Loading…</div>` : runs.length === 0
        ? html`<div class="empty">No runs yet. <a href="#/new">Start one</a>, or try <code>hive run "write a haiku about bees to haiku.txt" -b mock</code>.</div>`
        : html`<div class="tablewrap"><table>
            <thead><tr><th>Goal</th><th>Mode</th><th>Status</th><th>Started</th><th class="nowrap">Usage</th></tr></thead>
            <tbody>${runs.map((r) => html`<tr class="click" key=${r.id} onClick=${() => (location.hash = `#/runs/${r.id}`)}>
              <td><div class="ellipsis" style="max-width: 46ch" title=${r.goal}>${r.goal}</div><div class="muted small mono">${r.id} · ${r.project_slug}</div></td>
              <td>${r.mode}</td>
              <td><${Chip} status=${r.active && r.status === "running" ? "running" : r.status} /></td>
              <td class="nowrap">${fmtDate(r.started_at)}</td>
              <td class="nowrap muted small">${r.stats && r.stats.tokens != null ? `${fmtNum(r.stats.tokens)} tok · ${fmtDur(r.stats.seconds)}` : r.active ? "live" : ""}</td>
            </tr>`)}</tbody>
          </table></div>`}
    </section>
    <section class="side stack"><${BackendsPanel} backends=${backends} /></section>
  </div>`;
}

function BackendsPage() {
  const [backends, setBackends] = useState([]);
  const [models, setModels] = useState({});
  usePoll(async () => setBackends(await api("/backends")), 2000);
  const check = act(async () => setBackends(await api("/backends/check", { method: "POST" })));
  const listModels = act(async (name) => setModels({ ...models, [name]: await api(`/backends/${encodeURIComponent(name)}/models`) }));
  return html`<div class="stack" style="max-width: 900px">
    <section class="panel">
      <header><h2>Backends</h2><button onClick=${check}>Check now</button></header>
      <p class="muted small">Model servers from <code>backends.yaml</code>. Each one has its own queue and concurrency limit, so a slow device never gets more requests than it can take.</p>
      <div class="tablewrap"><table>
        <thead><tr><th>Name</th><th>Health</th><th>Where</th><th>Load</th><th>Speed</th><th></th></tr></thead>
        <tbody>${backends.map((b) => html`<tr key=${b.name}>
          <td><b>${b.name}</b><div class="muted small">${b.type}${b.device ? ` · device ${b.device}` : ""}</div></td>
          <td><${Chip} status=${b.health} />${b.last_error ? html`<div class="muted small" title=${b.last_error}>${clip(b.last_error, 60)}</div>` : ""}</td>
          <td class="small mono">${b.url || "(built in)"}<div class="muted">${b.default_model || ""}</div></td>
          <td class="small">${b.in_flight}/${b.concurrency} running · ${b.queue_depth} queued<div class="muted">${b.ok} ok · ${b.failed} failed</div></td>
          <td class="small">${b.tok_s != null ? `${b.tok_s} tok/s` : "–"}<div class="muted">${b.p50_ms != null ? `p50 ${fmtDur(b.p50_ms / 1000)}` : ""}</div></td>
          <td><button class="small" onClick=${() => listModels(b.name)}>Models</button>
            ${models[b.name] ? html`<div class="small muted" style="max-width: 220px">${models[b.name].join(", ") || "(none)"}</div>` : ""}</td>
        </tr>`)}</tbody>
      </table></div>
    </section>
  </div>`;
}

function MemoryPage() {
  const [data, setData] = useState({ agents: [], notes: [] });
  const [agent, setAgent] = useState("");
  const [q, setQ] = useState("");
  const [projects, setProjects] = useState([]);
  const [draft, setDraft] = useState({ agent: "", text: "", project: "" });
  const load = act(async () => {
    const qs = new URLSearchParams();
    if (agent) qs.set("agent", agent);
    if (q.trim()) qs.set("q", q.trim());
    setData(await api(`/memory?${qs}`));
  });
  useEffect(() => { load(); }, [agent, q]);
  useEffect(() => { api("/projects").then(setProjects).catch(() => {}); }, []);
  const forget = act(async (n) => { if (confirm(`Forget note ${n.id}?`)) { await api(`/memory/${n.id}`, { method: "DELETE" }); load(); } });
  const add = act(async (e) => {
    e.preventDefault();
    if (!draft.agent.trim() || !draft.text.trim()) return toast("Pick an agent and write the note.");
    await api("/memory", { method: "POST", body: { agent: draft.agent.trim(), text: draft.text, project: draft.project || null } });
    setDraft({ ...draft, text: "" });
    load();
  });
  return html`<div class="stack" style="max-width: 1000px">
    <section class="panel">
      <header><h2>Agent memory</h2><span class="muted small">${data.notes.length} shown</span></header>
      <p class="muted small">Notes agents keep across runs: facts about a project, conventions, lessons they wrote when a run ended.
        Each turn, an agent's most relevant notes are added to its prompt. Notes are written by models, so delete anything wrong.</p>
      <div class="row">
        <select value=${agent} onChange=${(e) => setAgent(e.target.value)} aria-label="Agent">
          <option value="">All agents</option>${data.agents.map((a) => html`<option value=${a.agent}>${a.agent} (${a.notes})</option>`)}</select>
        <input class="grow" placeholder=${agent ? "Search: ranks this agent's notes by relevance" : "Filter by text"} value=${q} onInput=${(e) => setQ(e.target.value)} />
      </div>
    </section>
    <section class="panel">
      ${data.notes.length === 0 ? html`<div class="empty">No notes yet. Agents write some at the end of each run.</div>` : html`<div class="tablewrap"><table>
        <thead><tr><th>Agent</th><th>Note</th><th>Where</th><th>Used</th><th></th></tr></thead>
        <tbody>${data.notes.map((n) => html`<tr key=${n.id}>
          <td class="nowrap"><b>${n.agent}</b><div class="muted small">${n.kind} · ${n.source}</div></td>
          <td>${n.text}${n.score ? html`<div class="muted small">relevance ${n.score}</div>` : ""}</td>
          <td class="small">${n.project || html`<span class="muted">all projects</span>`}<div class="muted">${fmtDate(n.created_at)}</div></td>
          <td class="small">${n.uses}×</td>
          <td><button class="small danger" onClick=${() => forget(n)}>Forget</button></td>
        </tr>`)}</tbody></table></div>`}
    </section>
    <form class="panel stack" onSubmit=${add}>
      <h2>Teach an agent</h2>
      <p class="muted small">A note you add here is pinned whenever it's relevant, in every run of that agent (by name).</p>
      <div class="row">
        <input list="mem-agents" placeholder="agent name, e.g. coder" value=${draft.agent} onInput=${(e) => setDraft({ ...draft, agent: e.target.value })} aria-label="Agent" />
        <datalist id="mem-agents">${data.agents.map((a) => html`<option value=${a.agent} />`)}</datalist>
        <select value=${draft.project} onChange=${(e) => setDraft({ ...draft, project: e.target.value })} aria-label="Project">
          <option value="">All projects</option>${projects.map((p) => html`<option value=${p.slug}>${p.slug}</option>`)}</select>
      </div>
      <textarea rows="2" placeholder="e.g. This project uses tabs, not spaces, and pytest for tests." value=${draft.text} onInput=${(e) => setDraft({ ...draft, text: e.target.value })}></textarea>
      <div><button class="primary" type="submit">Save note</button></div>
    </form>
  </div>`;
}

function BackendsPanel({ backends }) {
  const worst = backends.reduce((m, b) => (b.queue_depth > (m ? m.queue_depth : 0) ? b : m), null);
  return html`<div class="panel">
    <header><h2>Backend load</h2><a class="small" href="#/backends">details</a></header>
    ${backends.length === 0 ? html`<div class="empty">No backends</div>` : backends.map((b) => html`<div class="backend" key=${b.name}>
      <div class="row">
        <b class="grow ellipsis">${b.name}</b>
        ${worst && worst.name === b.name ? html`<span class="tag" title="Most requests waiting: this is the bottleneck">bottleneck</span>` : ""}
        <${Chip} status=${b.health} />
      </div>
      <div class="bar" title=${`${b.in_flight} of ${b.concurrency} slots busy`}><i style=${`width:${Math.min(100, (100 * b.in_flight) / Math.max(1, b.concurrency))}%`}></i></div>
      <div class="row small muted">
        <span>${b.in_flight}/${b.concurrency} busy</span>
        <span>${b.queue_depth} queued</span>
        <span class="right">${b.tok_s != null ? `${b.tok_s} tok/s` : ""}</span>
      </div>
      ${b.last_model ? html`<div class="small muted ellipsis">model: ${b.last_model}</div>` : ""}
    </div>`)}
  </div>`;
}

// --- new run --------------------------------------------------------------------------------------

function NewRun({ info }) {
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState("pipeline");
  const [team, setTeam] = useState([]);
  const [approval, setApproval] = useState("ask");
  const [workspace, setWorkspace] = useState("");
  const [limits, setLimits] = useState({});
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState({});

  const defaultTeam = (m) => {
    const md = info && info.modes.find((x) => x.name === m);
    return (md ? md.default_team : ["coder"]).map((role) => ({ role, name: "", backend: info ? info.default_backend : "", model: "" }));
  };
  useEffect(() => { if (info) setTeam(defaultTeam(mode)); }, [info]);
  useEffect(() => {
    for (const b of new Set(team.map((m) => m.backend))) {
      if (b && !models[b]) api(`/backends/${encodeURIComponent(b)}/models`).then((ms) => setModels((x) => ({ ...x, [b]: ms }))).catch(() => setModels((x) => ({ ...x, [b]: [] })));
    }
  }, [team]);

  if (!info) return html`<div class="empty">Loading…</div>`;
  const setMember = (i, k, v) => setTeam(team.map((m, j) => (j === i ? { ...m, [k]: v } : m)));
  const submit = act(async (e) => {
    e.preventDefault();
    if (!goal.trim()) return toast("Describe the goal first.");
    setBusy(true);
    try {
      const lim = {};
      if (limits.turns) lim.max_turns_per_run = +limits.turns;
      if (limits.tokens) lim.max_tokens_per_run = +limits.tokens;
      if (limits.minutes) lim.max_wall_seconds = Math.round(+limits.minutes * 60);
      if (limits.rounds) lim.max_rounds = +limits.rounds;
      const body = {
        goal, mode, approval, limits: lim, workspace: workspace || null,
        team: team.map((m) => Object.fromEntries(Object.entries(m).filter(([, v]) => v !== ""))),
      };
      const r = await api("/runs", { method: "POST", body });
      location.hash = `#/runs/${r.id}`;
    } finally { setBusy(false); }
  });

  return html`<form class="form" onSubmit=${submit}>
    <h2>New run</h2>
    <label><span>Goal, in plain English</span>
      <textarea rows="3" placeholder="Write a Python script that renames photos by the date they were taken, with a README" value=${goal} onInput=${(e) => setGoal(e.target.value)} autofocus></textarea>
    </label>
    <div>
      <h3 style="margin-bottom: 6px">Collaboration mode</h3>
      <div class="modes">${info.modes.map((m) => html`<div class=${"mode" + (m.name === mode ? " on" : "")} key=${m.name}
          onClick=${() => { setMode(m.name); setTeam(defaultTeam(m.name)); }} role="radio" aria-checked=${m.name === mode} tabindex="0">
        <b>${m.name}</b><span class="muted small">${m.description}</span></div>`)}</div>
    </div>
    <div class="stack">
      <div class="row"><h3 class="grow">Team</h3>
        <button type="button" class="small" onClick=${() => setTeam([...team, { role: "coder", name: "", backend: info.default_backend, model: "" }])}>Add agent</button>
        <button type="button" class="small ghost" onClick=${() => setTeam(defaultTeam(mode))}>Reset</button></div>
      ${team.map((m, i) => html`<div class="member" key=${i}>
        <select value=${m.role} onChange=${(e) => setMember(i, "role", e.target.value)} aria-label="Role">
          ${info.roles.map((r) => html`<option value=${r.name}>${r.name}</option>`)}</select>
        <input placeholder=${`name (${m.role})`} value=${m.name} onInput=${(e) => setMember(i, "name", e.target.value)} aria-label="Name" />
        <select value=${m.backend} onChange=${(e) => setMember(i, "backend", e.target.value)} aria-label="Backend">
          ${info.backends.map((b) => html`<option value=${b.name}>${b.name}</option>`)}</select>
        <input list=${`models-${i}`} placeholder="model (backend default)" value=${m.model} onInput=${(e) => setMember(i, "model", e.target.value)} aria-label="Model" />
        <datalist id=${`models-${i}`}>${(models[m.backend] || []).map((x) => html`<option value=${x} />`)}</datalist>
        <button type="button" class="ghost" onClick=${() => setTeam(team.filter((_, j) => j !== i))} aria-label="Remove">✕</button>
      </div>`)}
      <p class="muted small">Put the planner, coordinator and judge on your biggest model; small models do best in narrow roles like writer or tester.</p>
    </div>
    <details>
      <summary>Workspace, approvals and budgets</summary>
      <div class="stack" style="margin-top: 10px">
        <label><span>Project folder (empty: a new folder in Hive's data directory)</span>
          <input value=${workspace} onInput=${(e) => setWorkspace(e.target.value)} placeholder="/home/you/projects/photo-renamer" /></label>
        <label><span>Risky actions (shell commands, deletes)</span>
          <select value=${approval} onChange=${(e) => setApproval(e.target.value)}>
            <option value="ask">Ask me each time</option><option value="all">Allow all</option><option value="none">Refuse all</option></select></label>
        <div class="row">
          ${[["turns", "Max turns", info.limits.max_turns_per_run], ["tokens", "Max tokens", info.limits.max_tokens_per_run],
             ["minutes", "Max minutes", Math.round(info.limits.max_wall_seconds / 60)], ["rounds", "Review rounds", info.limits.max_rounds]].map(([k, label, def]) =>
            html`<label class="grow"><span>${label}</span><input type="number" min="1" placeholder=${def} value=${limits[k] || ""} onInput=${(e) => setLimits({ ...limits, [k]: e.target.value })} /></label>`)}
        </div>
      </div>
    </details>
    <div class="row"><button class="primary" type="submit" disabled=${busy}>${busy ? "Starting…" : "Run"}</button>
      <span class="muted small">You can pause, steer and roll back while it runs.</span></div>
  </form>`;
}

// --- run view -------------------------------------------------------------------------------------

function useRunStream(id) {
  const stateRef = useRef(initState());
  const eventsRef = useRef([]);
  const [, setTick] = useState(0);
  const [load, setLoad] = useState([]);
  const [synced, setSynced] = useState(false);
  const [connected, setConnected] = useState(false);
  const [ended, setEnded] = useState(false);
  const pending = useRef(false);
  const bump = useCallback(() => {
    if (pending.current) return;
    pending.current = true;
    requestAnimationFrame(() => { pending.current = false; setTick((t) => t + 1); });
  }, []);
  useEffect(() => {
    stateRef.current = initState();
    eventsRef.current = [];
    const es = new EventSource(`/api/runs/${id}/stream`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (m) => {
      const e = JSON.parse(m.data);
      if (e.seq) {
        if (e.seq <= stateRef.current.lastSeq) return; // already have it (reconnect)
        eventsRef.current.push(e);
      }
      apply(stateRef.current, e);
      bump();
    };
    es.addEventListener("backends", (m) => setLoad(JSON.parse(m.data)));
    es.addEventListener("synced", () => { setSynced(true); bump(); });
    es.addEventListener("end", () => { es.close(); setEnded(true); });
    return () => es.close();
  }, [id]);
  // A finished run's stream ends; keep the load panel fresh by polling instead.
  usePoll(async () => { if (ended) setLoad(await api("/backends")); }, 2000, [ended]);
  return { state: stateRef.current, events: eventsRef.current, load, synced, connected: connected || ended };
}

function RunView({ id }) {
  const { state: live, events, load, synced, connected } = useRunStream(id);
  const [detail, setDetail] = useState(null);
  const [tab, setTab] = useState("feed");
  const [replay, setReplay] = useState(null); // {pos, playing, speed}
  const [editing, setEditing] = useState(null);
  const [to, setTo] = useState("#channel");
  const status = live.run.status;

  useEffect(() => { api(`/runs/${id}`).then(setDetail).catch((e) => toast(e.message)); }, [id, status, live.checkpoints.length]);

  // Replay: the same reducer over a prefix of the log.
  const s = useMemo(() => (replay ? reduceAll(events, replay.pos) : live), [replay && replay.pos, replay ? events.length : live]);
  useEffect(() => {
    if (!replay || !replay.playing) return;
    if (replay.pos >= events.length) { setReplay({ ...replay, playing: false }); return; }
    const cur = events[replay.pos - 1], next = events[replay.pos];
    const gap = cur && next ? (next.ts - cur.ts) * 1000 / replay.speed : 200;
    const t = setTimeout(() => setReplay((r) => r && { ...r, pos: r.pos + 1 }), Math.max(25, Math.min(1500, gap)));
    return () => clearTimeout(t);
  }, [replay]);

  const active = detail ? detail.active : !FINAL.includes(status);
  const controls = active && !replay;
  const post = (path, body = {}) => api(`/runs/${id}${path}`, { method: "POST", body });
  const specs = Object.fromEntries(((detail && detail.config && detail.config.team) || []).map((a) => [a.name, a]));
  const approvals = pendingApprovals(s);
  const limits = s.run.limits || {};
  const elapsed = (s.run.ended || (replay ? s.lastTs : Date.now() / 1000)) - (s.run.started || s.firstTs || Date.now() / 1000);

  if (!synced && events.length === 0) return html`<div class="empty">${connected ? "Loading run…" : "Connecting…"}</div>`;

  return html`<div>
    <div class="runhead">
      <div class="row">
        <div class="grow"><div class="goal">${s.run.goal}</div>
          <div class="muted small">${s.run.mode} mode · ${s.run.project || ""} · <span class="mono">${id}</span>${s.run.workspace ? html` · <span class="mono" title="workspace">${s.run.workspace}</span>` : ""}</div></div>
        <${Chip} status=${replay ? "paused" : status} label=${replay ? "replay" : null} />
      </div>
      <div class="row">
        <${Meter} label="Tokens" value=${s.tokens} max=${limits.max_tokens_per_run} fmt=${fmtNum} />
        <${Meter} label="Turns" value=${s.turns} max=${limits.max_turns_per_run} />
        <${Meter} label="Time" value=${elapsed} max=${limits.max_wall_seconds} fmt=${fmtDur} />
      </div>
      <div class="row">
        ${controls ? html`
          <button onClick=${act(() => post("/pause"))}>Pause all</button>
          <button onClick=${act(() => post("/resume"))}>Resume all</button>
          <button onClick=${act(() => post("/checkpoints", { label: "manual" }))}>Checkpoint</button>
          <button class="danger" onClick=${act(() => confirm("Stop this run?") && post("/stop"))}>Stop</button>` : ""}
        <a class="btn" href=${`/api/runs/${id}/export.md`} download>Export .md</a>
        <span class="spacer grow"></span>
        ${replay ? html`<button onClick=${() => setReplay(null)}>Back to live</button>`
                 : html`<button onClick=${() => setReplay({ pos: 0, playing: true, speed: 4 })} disabled=${!events.length}>Replay</button>`}
        ${!connected && active ? html`<span class="muted small">reconnecting…</span>` : ""}
      </div>
      ${replay ? html`<div class="replay panel">
        <button onClick=${() => setReplay({ ...replay, playing: !replay.playing, pos: replay.pos >= events.length ? 0 : replay.pos })}>${replay.playing ? "Pause" : "Play"}</button>
        <input type="range" min="0" max=${events.length} value=${replay.pos} onInput=${(e) => setReplay({ ...replay, pos: +e.target.value, playing: false })} aria-label="Replay position" />
        <span class="mono small">${replay.pos}/${events.length}${events[replay.pos - 1] ? " · " + fmtTime(events[replay.pos - 1].ts) : ""}</span>
        <select value=${replay.speed} onChange=${(e) => setReplay({ ...replay, speed: +e.target.value })} aria-label="Speed">
          ${[1, 4, 16, 64].map((x) => html`<option value=${x}>${x}×</option>`)}</select>
      </div>` : ""}
      ${s.run.attention && !replay ? html`<div class="banner"><b>Needs you:</b><span class="grow">${s.run.attention}</span>
        ${controls ? html`<button onClick=${act(() => post("/resume"))}>Resume</button>` : ""}</div>` : ""}
      ${s.run.finished ? html`<div class=${"banner" + (s.run.finished.status === "done" ? " done" : "")}>
        <div class="grow"><b>${s.run.finished.status === "done" ? "Done" : `Ended: ${s.run.finished.status.replace(/_/g, " ")}`}</b>
          ${s.run.finished.reason ? html`<div class="small">${s.run.finished.reason}</div>` : ""}
          ${s.run.finished.summary ? html`<pre class="small" style="margin-top:6px">${s.run.finished.summary}</pre>` : ""}</div>
        ${!replay && !active && s.run.finished.status !== "done" ? html`<button onClick=${act(async () => {
          const more = s.run.finished.status === "budget_exhausted" ? { limits: { max_tokens_per_run: (limits.max_tokens_per_run || 0) * 2,
            max_turns_per_run: (limits.max_turns_per_run || 0) * 2, max_wall_seconds: (limits.max_wall_seconds || 0) * 2 } } : {};
          await post("/resume-run", more); location.reload(); })}
          title="Continue from the last checkpoint">${s.run.finished.status === "budget_exhausted" ? "Resume with double budget" : "Resume"}</button>` : ""}</div>` : ""}
    </div>

    <div class="run-grid">
      <section class="stack">
        ${s.order.map((n) => html`<${AgentCard} key=${n} a=${s.agents[n]} spec=${specs[n]} controls=${controls}
          onPause=${act(() => post("/pause", { agent: n }))} onResume=${act(() => post("/resume", { agent: n }))}
          onInterrupt=${act(() => post("/interrupt", { agent: n }))} onEdit=${() => setEditing(n)}
          onMessage=${() => { setTo(n); setTab("feed"); }} />`)}
      </section>

      <section class="panel">
        <div class="tabs" role="tablist">
          ${[["feed", "Feed", s.feed.length], ["tasks", "Tasks", Object.keys(s.tasks).length], ["board", "Board", s.board.length],
             ["files", "Files", Object.keys(s.files).length], ["checkpoints", "Checkpoints", s.checkpoints.length]].map(([k, label, n]) =>
            html`<button role="tab" class=${tab === k ? "on" : ""} onClick=${() => setTab(k)}>${label}<span class="count">${n || ""}</span></button>`)}
        </div>
        ${tab === "feed" ? html`<${Feed} items=${s.feed} follow=${!replay} />
          ${controls ? html`<${Composer} agents=${s.order} to=${to} setTo=${setTo} send=${(text) => post("/message", { to, text })} />` : ""}` : ""}
        ${tab === "tasks" ? html`<${Tasks} tasks=${s.tasks} />` : ""}
        ${tab === "board" ? html`<${Board} entries=${s.board} />` : ""}
        ${tab === "files" ? html`<${Files} id=${id} files=${s.files} />` : ""}
        ${tab === "checkpoints" ? html`<${Checkpoints} items=${s.checkpoints} controls=${controls}
          rollback=${act(async (cp) => { if (confirm(`Roll back to "${cp.label}"? Files, tasks, board and agent memory go back to that point.`)) await post("/rollback", { checkpoint: cp.id }); })} />` : ""}
      </section>

      <section class="side stack">
        ${approvals.length && controls ? html`<div class="panel stack"><h2>Waiting for approval</h2>
          ${approvals.map((ap) => html`<${Approval} key=${ap.id} ap=${ap} decide=${(ok, reason) => post(`/approvals/${ap.id}`, { approved: ok, reason })} />`)}</div>` : ""}
        <${BackendsPanel} backends=${load} />
      </section>
    </div>
    ${editing && specs[editing] ? html`<${EditAgent} spec=${specs[editing]} onClose=${() => setEditing(null)}
      save=${act(async (changes) => { await api(`/runs/${id}/agents/${encodeURIComponent(editing)}`, { method: "PATCH", body: changes }); setEditing(null);
        setDetail(await api(`/runs/${id}`)); })} />` : ""}
  </div>`;
}

function Meter({ label, value, max, fmt = (x) => x }) {
  const pct = max ? Math.min(100, (100 * value) / max) : 0;
  return html`<div class="meter"><div class="row"><span>${label}</span><span class="right mono">${fmt(value)}${max ? ` / ${fmt(max)}` : ""}</span></div>
    <div class="bar"><i style=${`width:${pct}%; background:${pct > 90 ? "var(--bad)" : pct > 70 ? "var(--warn)" : "var(--accent)"}`}></i></div></div>`;
}

function AgentCard({ a, spec, controls, onPause, onResume, onInterrupt, onEdit, onMessage }) {
  const streamRef = useRef(null);
  useEffect(() => { if (streamRef.current) streamRef.current.scrollTop = streamRef.current.scrollHeight; }, [a.stream]);
  const busy = ["thinking", "waiting_backend", "waiting_approval"].includes(a.status);
  const text = a.stream || a.reply;
  return html`<div class=${"agent" + (busy ? " active" : "")}>
    <div class="row"><span class="name grow ellipsis">${a.name}</span><${Chip} status=${a.status} /></div>
    <div class="meta"><span>${a.role}</span><span class="mono">${a.backend}${a.model ? "/" + a.model : ""}</span>
      <span>${a.turns} turns</span><span>${fmtNum(a.tokens)} tok</span>${a.last && a.last.tok_s ? html`<span>${a.last.tok_s} tok/s</span>` : ""}</div>
    ${a.detail ? html`<div class="small muted ellipsis">${a.detail}</div>` : ""}
    <div ref=${streamRef} class=${"stream" + (text ? "" : " empty")}>${text ? clip(text, 2500) : "nothing yet"}</div>
    ${controls ? html`<div class="row small">
      ${a.status === "paused" ? html`<button onClick=${onResume}>Resume</button>` : html`<button onClick=${onPause}>Pause</button>`}
      <button onClick=${onInterrupt} disabled=${!busy} title="Cancel the current turn">Interrupt</button>
      <button onClick=${onEdit} disabled=${!spec}>Edit</button>
      <button onClick=${onMessage}>Message</button>
    </div>` : ""}
  </div>`;
}

function Feed({ items, follow }) {
  const ref = useRef(null);
  const [limit, setLimit] = useState(300);
  const atBottom = useRef(true);
  useEffect(() => { if (ref.current && follow && atBottom.current) ref.current.scrollTop = ref.current.scrollHeight; });
  const shown = items.slice(-limit);
  return html`<div class="feed" ref=${ref} onScroll=${(e) => { const el = e.target; atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}>
    ${items.length > limit ? html`<button class="ghost small" onClick=${() => setLimit(limit + 500)}>Show earlier (${items.length - limit} more)</button>` : ""}
    ${shown.length === 0 ? html`<div class="empty">Nothing yet.</div>` : ""}
    ${shown.map((it, i) => html`<${FeedItem} key=${(it.seq || 0) + ":" + i} it=${it} />`)}
  </div>`;
}

function FeedItem({ it }) {
  const cls = `item ${it.type}${it.cls ? " " + it.cls : ""}${it.rolledBack ? " rb" : ""}`;
  let body;
  if (it.type === "tool") {
    const argText = JSON.stringify(it.args || {}, null, 1);
    body = html`<span class="who">${it.agent}</span><code>${it.tool}</code>
      <span class="muted small"> ${clip(Object.values(it.args || {}).filter((v) => typeof v === "string").join(" · "), 90)}</span>
      <details><summary class="small">${it.result === undefined ? "running…" : it.ok ? "result" : "error"}</summary>
        <pre>${argText}</pre>${it.result !== undefined ? html`<pre>${it.result}</pre>` : ""}</details>`;
  } else if (it.type === "msg") {
    body = html`<span class="who">${it.from} → ${it.to}</span>${it.text}`;
  } else if (it.type === "turn") {
    body = html`<span class="who">${it.agent}</span><span class="muted">turn ${it.turn}</span><details><summary class="small">${clip(it.text.split("\n")[0], 100)}</summary><pre>${it.text}</pre></details>`;
  } else if (it.type === "memory") {
    body = html`<span class="who">${it.agent}</span>${it.text}<details><summary class="small">notes</summary>
      <ul class="small" style="margin:4px 0; padding-left: 18px">${it.notes.map((n) => html`<li key=${n.id}>${n.text}</li>`)}</ul></details>`;
  } else if (it.type === "reply") {
    body = html`<span class="who">${it.agent}</span><pre>${it.text}</pre>`;
  } else {
    body = html`${it.agent ? html`<span class="who">${it.agent}</span>` : ""}${it.text}`;
  }
  return html`<div class=${cls}><span class="t">${it.ts ? fmtTime(it.ts) : ""}</span><span class="ic">${it.icon}</span><div class="body">${body}</div></div>`;
}

function Composer({ agents, to, setTo, send }) {
  const [text, setText] = useState("");
  const go = act(async () => { if (!text.trim()) return; await send(text); setText(""); });
  return html`<div class="row" style="margin-top: 10px; align-items: flex-end">
    <select value=${to} onChange=${(e) => setTo(e.target.value)} aria-label="Send to">
      <option value="#channel">everyone</option>${agents.map((n) => html`<option value=${n}>${n}</option>`)}</select>
    <textarea class="grow" rows="2" placeholder="Message the team as yourself (Ctrl+Enter sends)" value=${text}
      onInput=${(e) => setText(e.target.value)} onKeyDown=${(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) go(); }}></textarea>
    <button class="primary" onClick=${go}>Send</button>
  </div>`;
}

function Tasks({ tasks }) {
  const cols = [["todo", "To do"], ["in_progress", "In progress"], ["done", "Done"], ["failed", "Failed"]];
  const all = Object.values(tasks);
  if (!all.length) return html`<div class="empty">No tasks yet.</div>`;
  return html`<div class="kanban">${cols.map(([k, label]) => html`<div class="col"><h3>${label} <span class="muted">${all.filter((t) => t.status === k).length}</span></h3>
    ${all.filter((t) => t.status === k).map((t) => html`<div class="task" key=${t.id}>
      <div><span class="tid">#${t.id}</span> ${t.title}</div>
      <div class="small muted">${t.owner ? `@${t.owner}` : "unassigned"}${t.depends_on.length ? ` · after #${t.depends_on.join(", #")}` : ""}</div>
      ${t.result ? html`<div class="res" title=${t.result}>${clip(t.result, 160)}</div>` : ""}
      ${t.notes && t.notes.length ? html`<div class="res">${clip(t.notes[t.notes.length - 1], 160)}</div>` : ""}
    </div>`)}</div>`)}</div>`;
}

function Board({ entries }) {
  if (!entries.length) return html`<div class="empty">The board is empty.</div>`;
  return html`<div class="stack">${entries.slice().reverse().map((e) => html`<div class="task" key=${e.id}>
    <div class="row"><${Chip} status=${e.kind === "deliverable" ? "done" : e.kind === "decision" ? "review" : "idle"} label=${e.kind} />
      <b class="grow">${e.title}</b><span class="muted small">${e.author} · ${fmtTime(e.ts)}</span></div>
    ${e.content ? html`<pre class="small" style="margin-top:6px">${e.content}</pre>` : ""}</div>`)}</div>`;
}

function Files({ id, files }) {
  const paths = Object.keys(files).sort();
  const [sel, setSel] = useState(null);
  const [diff, setDiff] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const agents = [...new Set(Object.values(files).flat().map((v) => v.agent))];
  const show = act(async (path, version) => {
    setSel({ path, version });
    setDiff(await api(`/runs/${id}/diff?path=${encodeURIComponent(path)}&version=${version}`));
  });
  if (!paths.length) return html`<div class="empty">No files changed yet.</div>`;
  const visible = paths.filter((p) => !agentFilter || files[p].some((v) => v.agent === agentFilter));
  return html`<div class="files">
    <div class="filelist stack">
      <select value=${agentFilter} onChange=${(e) => setAgentFilter(e.target.value)} aria-label="Filter by agent">
        <option value="">All agents</option>${agents.map((a) => html`<option value=${a}>${a}</option>`)}</select>
      ${visible.map((p) => html`<div key=${p}>
        <div class="mono small" style="padding: 4px 6px; font-weight: 600">${p}</div>
        ${files[p].filter((v) => !agentFilter || v.agent === agentFilter).map((v) => html`<button key=${v.version}
          class=${sel && sel.path === p && sel.version === v.version ? "on" : ""} onClick=${() => show(p, v.version)}>
          <span>v${v.version} · ${v.agent}</span><span class="small"><span style="color:var(--ok)">+${v.added}</span> <span style="color:var(--bad)">−${v.removed}</span>${v.deleted ? " deleted" : ""}</span></button>`)}
      </div>`)}
    </div>
    <div>${sel ? html`<div class="row" style="margin-bottom:6px"><b class="mono grow">${sel.path} v${sel.version}</b>
        <a class="small" href=${`/api/runs/${id}/file?path=${encodeURIComponent(sel.path)}&version=${sel.version}`} target="_blank">raw</a></div>
      <${Diff} text=${diff} />` : html`<div class="empty">Pick a version to see what changed.</div>`}</div>
  </div>`;
}

function Diff({ text }) {
  if (!text) return html`<div class="empty">No changes.</div>`;
  return html`<div class="diff">${text.split("\n").map((l, i) => html`<div key=${i} class=${l.startsWith("+++") || l.startsWith("---") ? "h" : l.startsWith("+") ? "a" : l.startsWith("-") ? "d" : l.startsWith("@@") ? "h" : ""}>${l || " "}</div>`)}</div>`;
}

function Checkpoints({ items, controls, rollback }) {
  if (!items.length) return html`<div class="empty">No checkpoints yet.</div>`;
  return html`<table><thead><tr><th>#</th><th>When</th><th>Label</th><th></th></tr></thead><tbody>
    ${items.slice().reverse().map((c) => html`<tr key=${c.id}><td class="mono">${c.id}</td><td class="nowrap">${fmtTime(c.ts)}</td><td>${c.label}</td>
      <td>${controls ? html`<button class="small" onClick=${() => rollback(c)}>Roll back here</button>` : ""}</td></tr>`)}
  </tbody></table>`;
}

function Approval({ ap, decide }) {
  const [reason, setReason] = useState("");
  const main = ap.args.command || ap.args.path || JSON.stringify(ap.args);
  return html`<div class="approval">
    <div><b>${ap.agent}</b> wants to <code>${ap.tool}</code></div>
    <pre class="mono small">${main}</pre>
    <input placeholder="Reason (optional, shown to the agent)" value=${reason} onInput=${(e) => setReason(e.target.value)} />
    <div class="row"><button class="primary" onClick=${act(() => decide(true, reason))}>Allow</button>
      <button class="danger" onClick=${act(() => decide(false, reason))}>Refuse</button></div>
  </div>`;
}

function EditAgent({ spec, onClose, save }) {
  const [prompt, setPrompt] = useState(spec.system_prompt);
  const [model, setModel] = useState(spec.model || "");
  const [temp, setTemp] = useState(spec.params && spec.params.temperature != null ? spec.params.temperature : "");
  const submit = () => {
    const changes = {};
    if (prompt !== spec.system_prompt) changes.system_prompt = prompt;
    if ((model || null) !== (spec.model || null)) changes.model = model || null;
    const t = temp === "" ? undefined : +temp;
    if (t !== (spec.params || {}).temperature) changes.params = { ...(spec.params || {}), temperature: t };
    if (!Object.keys(changes).length) return onClose();
    save(changes);
  };
  return html`<${Modal} title=${`Edit ${spec.name}`} onClose=${onClose}>
    <p class="muted small">Changes apply from the agent's next model call.</p>
    <label><span>System prompt</span><textarea rows="12" value=${prompt} onInput=${(e) => setPrompt(e.target.value)}></textarea></label>
    <div class="row"><label class="grow"><span>Model on ${spec.backend}</span><input value=${model} placeholder="backend default" onInput=${(e) => setModel(e.target.value)} /></label>
      <label><span>Temperature</span><input type="number" step="0.1" min="0" max="2" value=${temp} onInput=${(e) => setTemp(e.target.value)} /></label></div>
    <div class="row"><button class="primary" onClick=${submit}>Save</button><button onClick=${onClose}>Cancel</button></div>
  <//>`;
}

render(html`<${App} />`, document.getElementById("app"));
