// ShutOff UI: renders the FalkorDB network snapshot, the agent's plan and
// the evidence trace. No framework; everything comes from /api.
"use strict";

const SVGNS = "http://www.w3.org/2000/svg";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = {
  session: "demo-" + Math.random().toString(36).slice(2, 8),
  net: null,
  byId: {},
  plan: null,          // last plan_isolation result
  check: null,         // last check_closure_plan result
  issued: null,        // last issue_work_order result
  proposal: null,
  overlay: { seg: new Set(), cut: new Set(), planValves: new Set(), proposed: new Set(), paths: [], breakPipe: null, critical: {} },
  busy: false,
};

// ------------------------------------------------------------------ api
async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

async function loadNetwork() {
  state.net = await api("/api/network");
  state.byId = {};
  for (const n of state.net.nodes) state.byId[n.id] = n;
  renderMap();
  renderWorkOrders();
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    const db = $("#db-badge");
    db.textContent = h.falkordb ? `FalkorDB · ${h.nodes} nodes` : "FalkorDB offline";
    db.className = "badge " + (h.falkordb ? "ok" : "bad");
    const m = $("#mode-badge");
    m.textContent = h.mode === "llm" ? `Agent: Claude (${h.model})` : "Agent: offline planner (deterministic)";
    m.title = h.mode === "llm" ? "Claude chooses the tools; replies are checked against graph results."
      : "No ANTHROPIC_API_KEY set: a deterministic intent parser chooses the same tools.";
  } catch (e) {
    $("#db-badge").textContent = "API offline";
    $("#db-badge").className = "badge bad";
  }
}

// ------------------------------------------------------------------ chat
function addMsg(role, text, meta) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = text;
  if (meta) {
    const m = document.createElement("div");
    m.className = "meta";
    m.innerHTML = meta;
    div.appendChild(m);
  }
  $("#log").appendChild(div);
  $("#log").scrollTop = $("#log").scrollHeight;
  return div;
}

function metaFor(r) {
  const tags = [];
  const modeName = { offline: "offline planner", llm: "Claude", "human-action": "human action" }[r.mode] || r.mode;
  tags.push(`<span class="tag ${r.mode === "human-action" ? "h" : ""}">${esc(modeName)}</span>`);
  const nq = r.steps.reduce((a, s) => a + s.queries.length, 0);
  if (r.steps.length) tags.push(`<span class="tag">${r.steps.length} tool call${r.steps.length > 1 ? "s" : ""} · ${nq} queries</span>`);
  if (r.mode === "llm") {
    tags.push(r.verified ? `<span class="tag v">✓ reply checked against graph</span>`
      : `<span class="tag x" title="${esc(r.note)}">✗ model reply rejected, rebuilt from graph</span>`);
  } else if (r.steps.length) {
    tags.push(`<span class="tag v">✓ built from graph results</span>`);
  }
  if (r.note && r.mode !== "llm") tags.push(`<span class="tag x">${esc(r.note)}</span>`);
  return tags.join("");
}

async function send(text, endpoint = "/api/chat", extra = {}) {
  if (state.busy) return;
  state.busy = true;
  setBusy(true);
  if (endpoint === "/api/chat") addMsg("user", text);
  const typing = addMsg("agent", "Querying the graph…");
  typing.classList.add("typing");
  try {
    const r = await api(endpoint, { session: state.session, text, ...extra });
    typing.remove();
    addMsg("agent", r.reply, metaFor(r));
    applySteps(r);
    renderEvidence(r);
    await loadNetwork();
    renderPlan();
  } catch (e) {
    typing.remove();
    addMsg("agent", "Error: " + e.message, `<span class="tag x">request failed</span>`);
  } finally {
    state.busy = false;
    setBusy(false);
  }
}

function setBusy(b) {
  for (const el of document.querySelectorAll("#composer button, #chips button, .approve")) el.disabled = b || (el.classList.contains("approve") && !canApprove());
}

// ------------------------------------------------------------------ overlays from tool results
function applySteps(r) {
  const o = state.overlay;
  state.proposal = r.proposal;
  for (const st of r.steps) {
    const res = st.result;
    if (st.tool === "plan_isolation" && res.simulation) {
      state.plan = res; state.check = null; state.issued = null;
      o.seg = new Set(res.segment.pipes);
      o.cut = new Set(res.simulation.dry_pipes.filter((p) => !res.segment.pipes.includes(p)));
      o.planValves = new Set(res.close.map((v) => v.id));
      o.proposed = new Set();
      o.paths = [];
      o.breakPipe = res.pipe.id;
      o.critical = {};
      for (const c of res.simulation.critical_status) o.critical[c.id] = c.supplied;
    } else if (st.tool === "check_closure_plan" && res.simulation) {
      state.check = res;
      o.proposed = new Set(res.proposed);
      o.paths = res.simulation.leak_path ? [{ path: res.simulation.leak_path.path, kind: "leak" }] : [];
    } else if (st.tool === "supply_check" && res.path) {
      o.paths = [{ path: res.path.path, kind: "supply" }];
    } else if (st.tool === "valve_exercise_priorities" && res.ranked) {
      o.proposed = new Set(res.ranked.slice(0, 5).map((x) => x.valve));
      o.paths = [];
    } else if (st.tool === "issue_work_order" && res.outcome === "issued") {
      state.issued = res;
      o.planValves = new Set();
      o.proposed = new Set();
    } else if (st.tool === "restore_service" && res.outcome === "restored") {
      state.plan = null; state.check = null; state.issued = null;
      state.overlay = { seg: new Set(), cut: new Set(), planValves: new Set(), proposed: new Set(), paths: [], breakPipe: null, critical: {} };
    } else if (st.tool === "locate_break" && res.outcome === "found") {
      o.breakPipe = res.pipe;
    }
  }
}

// ------------------------------------------------------------------ map
function el(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

function pipeOf(a, b) {
  if (a.kind === "Pipe") return a.id;
  if (b.kind === "Pipe") return b.id;
  if (a.kind === "Valve") return a.pipe;
  if (b.kind === "Valve") return b.pipe;
  return null;
}

function widthFor(p) {
  const d = p ? p.diameter_mm : 150;
  return d >= 400 ? 7 : d >= 200 ? 5 : d >= 150 ? 4 : 3;
}

function renderMap() {
  const svg = $("#map");
  svg.innerHTML = "";
  const net = state.net, by = state.byId, o = state.overlay;
  const dry = new Set(net.dry_pipes);
  const gStreets = el("g", {}, svg), gEdges = el("g", {}, svg), gHit = el("g", {}, svg), gPaths = el("g", {}, svg);
  const gNodes = el("g", {}, svg), gCust = el("g", {}, svg), gLabels = el("g", {}, svg);

  // street names
  for (const n of net.nodes) {
    if (n.kind !== "Junction" || !/^J-\d\d$/.test(n.id)) continue;
    const [street, ave] = n.name.split(" & ");
    if (n.id.endsWith("0")) el("text", { x: n.x + 85, y: n.y - 9, class: "street", "text-anchor": "middle" }, gStreets).textContent = street.toUpperCase();
    if (n.id[2] === "0") el("text", { x: n.x, y: n.y - 30, class: "street", "text-anchor": "middle" }, gStreets).textContent = ave.toUpperCase();
  }
  const we = by["J-WE"];
  if (we) el("text", { x: we.x - 10, y: we.y + 5, class: "street", "text-anchor": "end" }, gStreets).textContent = "WILLOW CT";

  // edges, coloured by the state of the pipe they belong to
  for (const e of net.edges) {
    const a = by[e.a], b = by[e.b];
    if (!a || !b) continue;
    const pid = pipeOf(a, b);
    let cls = "edge";
    if (o.seg.has(pid)) cls += " seg";
    else if (o.cut.has(pid)) cls += " cut";
    else if (dry.has(pid)) cls += " dry";
    el("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: cls, "stroke-width": widthFor(by[pid]) }, gEdges);
  }

  // invisible hit-lines for pipe tooltips
  const custByPipe = {};
  for (const c of net.customers) (custByPipe[c.pipe] ||= []).push(c);
  for (const p of net.nodes.filter((n) => n.kind === "Pipe")) {
    const line = el("line", { x1: p.ax, y1: p.ay, x2: p.bx, y2: p.by, class: "pipehit" }, gHit);
    const cs = custByPipe[p.id] || [];
    tip(line, `<b>${esc(p.id)}</b> · ${esc(p.label)}<br>${esc(p.material)}, ${esc(p.installed)}, ${esc(p.diameter_mm)} mm` +
      (cs.length ? `<br>${cs.map((c) => esc(c.name)).join("<br>")}` : ""));
  }

  // graph paths (evidence)
  for (const pth of o.paths) {
    const pts = pth.path.map((id) => by[id]).filter(Boolean).map((n) => `${n.x},${n.y}`).join(" ");
    el("polyline", { points: pts, class: "pathline" + (pth.kind === "leak" ? " leak" : "") }, gPaths);
  }

  // nodes
  for (const n of net.nodes) {
    if (n.kind === "Junction") el("circle", { cx: n.x, cy: n.y, r: 3.5, class: "junction" }, gNodes);
    if (n.kind === "Source") {
      const g = el("g", { class: "source" }, gNodes);
      if (n.source_kind === "reservoir") el("rect", { x: n.x - 22, y: n.y - 16, width: 44, height: 32, rx: 6 }, g);
      else el("circle", { cx: n.x, cy: n.y, r: 20 }, g);
      el("text", { x: n.x, y: n.y + (n.source_kind === "reservoir" ? 36 : 40), "text-anchor": "middle" }, g).textContent = n.name;
      tip(g, `<b>${esc(n.name)}</b><br>water source`);
    }
    if (n.kind === "Valve") {
      let cls = "valve";
      if (n.condition === "unknown") cls += " unknown";
      if (n.seized) cls += " seized";
      else if (n.status === "closed") cls += " closed";
      else if (o.planValves.has(n.id)) cls += " plan";
      else if (o.proposed.has(n.id)) cls += " proposed";
      const r = el("rect", { x: n.x - 5.5, y: n.y - 5.5, width: 11, height: 11, class: cls, rx: 2 }, gNodes);
      tip(r, `<b>${esc(n.id)}</b> · ${esc(n.location)}<br>status: ${esc(n.status)} · condition: ${esc(n.condition)}${n.seized ? " (SEIZED)" : ""}<br>last exercised ${esc(n.last_exercised)}`);
      if (o.planValves.has(n.id) || o.proposed.has(n.id) || n.seized) {
        el("text", { x: n.x + 8, y: n.y - 8, class: "vlabel" }, gLabels).textContent = n.id;
      }
    }
  }

  // notable customers
  const glyph = { hospital: "H", medical_priority: "D", care_home: "C", school: "S" };
  for (const c of net.customers) {
    if (!glyph[c.kind]) continue;
    const p = by[c.pipe];
    if (!p) continue;
    const horiz = Math.abs(p.ay - p.by) < 1;
    const cx = horiz ? p.x + 20 : p.x + 18, cy = horiz ? p.y - 18 : p.y + 14;
    let st = "idle";
    if (c.id in o.critical) st = o.critical[c.id] ? "ok" : "out";
    if (dry.has(c.pipe) || o.seg.has(c.pipe) || o.cut.has(c.pipe)) st = "out";
    const g = el("g", { class: "cust " + st }, gCust);
    el("circle", { cx, cy, r: 12 }, g);
    el("text", { x: cx, y: cy }, g).textContent = glyph[c.kind];
    tip(g, `<b>${esc(c.name)}</b><br>${c.critical ? "critical customer" : "sensitive customer"} · on ${esc(c.pipe)}` +
      (st === "out" ? "<br><b>loses water</b>" : st === "ok" ? "<br>still supplied (graph path verified)" : ""));
  }

  // break marker
  if (o.breakPipe && by[o.breakPipe]) {
    const p = by[o.breakPipe];
    el("circle", { cx: p.x, cy: p.y, r: 9, class: "break-ring" }, gLabels);
    el("circle", { cx: p.x, cy: p.y, r: 6, class: "break-dot" }, gLabels);
    el("text", { x: p.x, y: p.y + 26, class: "break-label", "text-anchor": "middle" }, gLabels).textContent = "BREAK";
  }
}

function tip(node, html) {
  const t = $("#tip");
  node.addEventListener("mouseenter", () => { t.innerHTML = html; t.hidden = false; });
  node.addEventListener("mousemove", (ev) => {
    const box = $(".mapbox").getBoundingClientRect();
    let x = ev.clientX - box.left + 14, y = ev.clientY - box.top + 14;
    if (x + 290 > box.width) x -= 300;
    t.style.left = x + "px"; t.style.top = y + "px";
  });
  node.addEventListener("mouseleave", () => { t.hidden = true; });
}

function renderWorkOrders() {
  const bar = $("#wo-bar");
  const wos = state.net.work_orders || [];
  if (!wos.length) { bar.innerHTML = `<span class="muted">No active work orders.</span>`; return; }
  bar.innerHTML = wos.map((w) => `<span class="wo"><b>${esc(w.id)}</b> ${esc(w.pipe_label)} · ${w.valves.length} valves closed · ${w.accounts_out} accts out
    <button data-wo="${esc(w.id)}">Restore</button></span>`).join("");
  for (const b of bar.querySelectorAll("button[data-wo]")) {
    b.onclick = () => { addMsg("user", `Restore ${b.dataset.wo}`); send("", "/api/restore", { work_order: b.dataset.wo }); };
  }
}

// ------------------------------------------------------------------ plan panel
function canApprove() {
  return !!(state.proposal && state.plan && state.plan.simulation.isolated && !state.issued);
}

function renderPlan() {
  const box = $("#plan");
  const p = state.plan;
  if (!p) {
    box.className = "plan empty";
    box.textContent = "No active break. Report one on the incident line.";
    return;
  }
  box.className = "plan";
  const sim = p.simulation;
  const crit = sim.critical_out.length;
  let h = `<div><b>${esc(p.pipe.label)}</b> <span class="mono">${esc(p.pipe.id)}</span>
    <div class="muted">${esc(p.pipe.material)}, installed ${esc(p.pipe.installed)} · ${p.history.length} earlier break${p.history.length === 1 ? "" : "s"} on record</div></div>`;
  h += `<div class="kpis" style="margin-top:10px">
    <div class="kpi"><b>${p.close.length}</b><span>valves to close</span></div>
    <div class="kpi"><b>${sim.accounts_out}</b><span>accounts out</span></div>
    <div class="kpi ${crit ? "bad" : "good"}"><b>${crit}</b><span>critical out</span></div></div>`;
  h += sim.isolated
    ? `<div class="alert green">Break isolated: verified by re-running connectivity on a sandbox copy of the graph.</div>`
    : `<div class="alert red">The break cannot be isolated with operable valves.</div>`;
  if (sim.unintended.length) {
    h += `<div class="alert red"><b>Unintended cut-off:</b> ${sim.unintended.map((c) => esc(c.name)).join(", ")}. These customers are outside the segment but their only supply runs through it.</div>`;
  }
  if (state.check) {
    const c = state.check;
    h += c.simulation.isolated
      ? `<div class="alert green">Crew plan ${esc(c.proposed.join(", "))} isolates the break.</div>`
      : `<div class="alert red">Crew plan ${esc(c.proposed.join(", "))} does <b>not</b> isolate the break: water still arrives from ${esc(c.simulation.leak_path?.source)} (path shown on map).</div>`;
  }
  h += `<h3>Close in this order</h3><ol>` + p.close.map((v) =>
    `<li><span class="mono">${esc(v.id)}</span> ${esc(v.location)}${v.condition !== "ok" ? ` <span class="status ${esc(v.condition)}">${esc(v.condition)} · ${esc(v.last_exercised)}</span>` : ""}</li>`).join("") + `</ol>`;
  h += `<h3>Critical customers</h3><ul>` + sim.critical_status.map((c) =>
    `<li>${esc(c.name)}: ${c.supplied ? `<span style="color:var(--ok)">supplied</span> from ${esc(c.path?.source)} (${c.path ? c.path.hops : "?"} hops)` : `<b style="color:var(--cut)">LOSES WATER</b>`}</li>`).join("") + `</ul>`;
  for (const w of p.warnings) h += `<div class="alert amber">${esc(w)}</div>`;
  if (p.contingencies.length) {
    h += `<h3>If a valve will not turn</h3><table class="cont"><tr><th>Fails</th><th>Close instead</th><th>Accts</th><th>New critical cut-off</th></tr>` +
      p.contingencies.map((c) => {
        const already = new Set(sim.critical_out.map((x) => x.name));
        const extra = c.critical_out.filter((n) => !already.has(n));
        return `<tr><td class="mono nowrap">${esc(c.if_fails)}</td><td class="nowrap">${c.close_instead.length} valves</td><td class="nowrap">${c.accounts_out}${c.extra_accounts ? ` (+${c.extra_accounts})` : ""}</td><td>${extra.length ? `<b style="color:var(--cut)">+ ${esc(extra.join(", "))}</b>` : `<span class="muted">no new</span>`}</td></tr>`;
      }).join("") + `</table>`;
  }
  if (state.issued) {
    const w = state.issued;
    h += `<div class="alert green"><b>${esc(w.work_order)}</b> issued. Valves closed in the graph; ${w.standard_notices} standard notices` +
      (w.priority_notices.length ? ` and PRIORITY notice for ${esc(w.priority_notices.join(", "))}` : "") + ` recorded. <i>Dispatch and notices are simulated.</i></div>`;
  } else {
    h += `<button class="primary approve" ${canApprove() ? "" : "disabled"}>Approve &amp; issue work order</button>
      <div class="approve-note">Human approval required. The server re-runs the simulation before closing anything. Crew dispatch and customer notices are simulated.</div>`;
  }
  box.innerHTML = h;
  const btn = box.querySelector(".approve");
  if (btn) btn.onclick = () => { addMsg("user", "Approve plan"); send("", "/api/approve"); };
}

// ------------------------------------------------------------------ evidence
const shown = new Set();
function qHtml(q) {
  // Show each distinct Cypher text once per turn; repeats just list name + stats.
  const first = !shown.has(q.cypher);
  shown.add(q.cypher);
  return `<div class="q"><div class="qh"><b>${esc(q.name)}</b><span>${/__sim|sandbox/.test(q.graph) ? "sandbox · " : ""}${q.rows} rows · ${q.ms} ms</span></div>
    ${first ? `<pre>${esc(q.cypher)}</pre>` : ""}</div>`;
}

function renderEvidence(r) {
  shown.clear();
  const ev = $("#evidence");
  if (!r.steps.length) return;
  ev.querySelector(".muted")?.remove();
  for (const d of ev.querySelectorAll("details.step")) d.open = false;
  const frag = document.createDocumentFragment();
  for (const st of r.steps) {
    const d = document.createElement("details");
    d.className = "step";
    d.open = true;
    const rows = st.queries.reduce((a, q) => a + q.rows, 0);
    d.innerHTML = `<summary><span class="tool">${esc(st.tool)}</span><span class="stats">${st.queries.length} queries · ${st.ms} ms</span></summary>
      <div class="args">${esc(JSON.stringify(st.args))}</div>` +
      st.queries.slice(0, 8).map(qHtml).join("") +
      (st.queries.length > 8 ? `<details class="q"><summary class="muted">${st.queries.length - 8} more queries (backup-plan simulations)</summary>${st.queries.slice(8).map(qHtml).join("")}</details>` : "");
    frag.appendChild(d);
  }
  ev.prepend(frag);
}

// ------------------------------------------------------------------ wiring
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const t = $("#input").value.trim();
  if (!t) return;
  $("#input").value = "";
  send(t);
});
for (const b of document.querySelectorAll("#chips button")) b.onclick = () => send(b.dataset.msg);
$("#reset-btn").onclick = async () => {
  if (state.busy) return;
  await api("/api/reset", {});
  state.plan = state.check = state.issued = state.proposal = null;
  state.overlay = { seg: new Set(), cut: new Set(), planValves: new Set(), proposed: new Set(), paths: [], breakPipe: null, critical: {} };
  $("#log").innerHTML = "";
  $("#evidence").innerHTML = `<p class="muted">Tool calls and Cypher appear here.</p>`;
  addMsg("agent", "Demo reset: synthetic network reloaded into FalkorDB, field reports cleared.");
  await loadNetwork();
  renderPlan();
};

(async function init() {
  await loadHealth();
  await loadNetwork();
  renderPlan();
  addMsg("agent", "ShutOff is watching the Riverbend network (synthetic). Report a main break with its street and cross streets, or try a scenario below.");
})();
