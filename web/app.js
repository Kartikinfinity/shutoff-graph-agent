// ShutOff operator workspace. Vanilla JS; every fact shown comes from /api
// responses (FalkorDB query results). Nothing here computes network facts.
"use strict";

const SVGNS = "http://www.w3.org/2000/svg";
const FULL_VIEW = [-40, 30, 1220, 660];
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const clock = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

const S = {
  session: "op-" + Math.random().toString(36).slice(2, 8),
  net: null, byId: {}, mode: "offline", model: null,
  inc: { state: "idle" },          // current incident (client view of the server session)
  plan: null, prevKpis: null, prevValves: new Set(),
  check: null, observation: null, issued: null, restored: null, dockMsg: null, ranking: null,
  proposal: null, steps: [],
  ov: freshOverlay(),
  view: FULL_VIEW.slice(), busy: false, preview: null, focus: null, restoreArmed: false,
};

function freshOverlay() {
  return { proposed: new Set(), loss: new Set(), planValves: new Set(), changed: new Set(), hyp: new Set(),
    paths: [], breakPipe: null, critical: {}, marked: new Set() };
}

// ------------------------------------------------------------------ api
async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    S.mode = h.mode; S.model = h.model;
    const db = $("#db-pill");
    db.textContent = h.falkordb ? `FalkorDB · ${h.nodes} nodes` : "FalkorDB unreachable";
    db.className = "pill " + (h.falkordb ? "ok" : "bad");
    const m = $("#mode-pill");
    m.textContent = h.mode === "llm" ? "Live model (Claude)" : "Deterministic planner";
    m.title = h.mode === "llm" ? "Claude selects the fixed graph tools; IDs and counts in its reply are checked against tool results."
      : "No model API key configured: a deterministic parser selects the same fixed graph tools.";
    return h.falkordb;
  } catch (e) {
    $("#db-pill").textContent = "Server unreachable";
    $("#db-pill").className = "pill bad";
    return false;
  }
}

async function loadNetwork() {
  S.net = await api("/api/network");
  S.byId = {};
  for (const n of S.net.nodes) S.byId[n.id] = n;
}

// ------------------------------------------------------------------ sending
async function send(text, endpoint = "/api/chat", extra = {}, label = null) {
  if (S.busy) return;
  setBusy(true);
  if (label !== false) addEntry("user", label || "You", text || "", null);
  try {
    const r = await api(endpoint, { session: S.session, text, ...extra });
    if (r.steps.some((s) => s.tool === "locate_break")) S.steps = [];  // evidence is per incident
    S.steps.push(...r.steps);
    applyResponse(r, text);
    await loadNetwork();
    addEntry(kindOf(r), labelOf(r), headlineFor(r), r);
  } catch (e) {
    addEntry("warn", "Error", "The request failed: " + e.message, null);
    S.dockMsg = { kind: "bad", text: "The last request failed. Check that the server and FalkorDB are running, then retry." };
  } finally {
    setBusy(false);
    renderAll();
  }
}

function setBusy(b) {
  S.busy = b;
  $("#progress").hidden = !b;
  for (const el of document.querySelectorAll("#composer button, #composer input, .starter button, .dock button, .rail button.row, .cand button"))
    el.disabled = b;
  if (b) { $("#state-chip").dataset.state = "working"; $("#state-chip").textContent = "Working"; $("#strip-sub").textContent = "Querying the graph and simulating on a copy of it…"; }
}

// ------------------------------------------------------------------ state from tool results
function kpisOf(plan) {
  const sim = plan.simulation;
  return { valves: plan.close.length, accounts: sim.accounts_out, critical: sim.critical_out.length };
}

function applyResponse(r, text) {
  for (const st of r.steps) {
    const res = st.result;
    switch (st.tool) {
      case "locate_break":
        // a new report starts a new incident; the previous plan is gone
        S.plan = null; S.prevKpis = null; S.prevValves = new Set(); S.check = null; S.issued = null;
        S.restored = null; S.dockMsg = null; S.preview = null; S.focus = null; S.observation = null;
        S.ov = freshOverlay();
        S.inc = res.outcome === "found"
          ? { state: "working", pipe: res.pipe, label: res.label, report: text }
          : { state: "locate", report: text, locate: res };
        break;
      case "plan_isolation":
        if (res.outcome === "planned" || res.outcome === "cannot_isolate") {
          const same = S.plan && S.plan.pipe.id === res.pipe.id;
          S.prevKpis = same ? kpisOf(S.plan) : null;
          S.prevValves = same ? new Set(S.plan.close.map((v) => v.id)) : new Set();
          S.plan = res; S.check = same ? S.check : null; S.dockMsg = null; S.issued = null; S.restored = null; S.preview = null;
          const ov = S.ov;
          ov.proposed = new Set(res.segment.pipes);
          ov.loss = new Set(res.simulation.dry_pipes.filter((p) => !res.segment.pipes.includes(p)));
          ov.planValves = new Set(res.close.map((v) => v.id));
          ov.changed = same ? new Set([...ov.planValves].filter((v) => !S.prevValves.has(v))) : new Set();
          ov.hyp = new Set(); ov.marked = new Set(); ov.paths = [];
          ov.breakPipe = res.pipe.id;
          ov.critical = {};
          for (const c of res.simulation.critical_status) ov.critical[c.id] = c.supplied;
          S.inc = { ...S.inc, pipe: res.pipe.id, label: res.pipe.label,
            state: res.outcome === "cannot_isolate" ? "cannot" : (res.simulation.critical_out.length ? "ack" : "ready") };
        } else {
          S.plan = null; S.inc = { state: "locate", report: text, locate: res };
        }
        break;
      case "check_closure_plan":
        S.check = res;
        S.ov.paths = res.simulation && res.simulation.leak_path ? [{ path: res.simulation.leak_path.path, kind: "leak" }] : [];
        break;
      case "report_valve_condition":
        if (res.outcome === "recorded") S.observation = { valve: res.valve, condition: res.condition, at: clock() };
        break;
      case "supply_check":
        if (res.path) S.ov.paths = [{ path: res.path.path, kind: "supply" }];
        break;
      case "valve_exercise_priorities":
        S.ranking = res;
        S.ov.marked = new Set(res.ranked.slice(0, 5).map((x) => x.valve));
        break;
      case "issue_work_order":
        if (res.outcome === "issued") {
          S.issued = res; S.dockMsg = null; S.inc.state = "issued";
          S.ov.proposed = new Set(); S.ov.loss = new Set(); S.ov.planValves = new Set(); S.ov.changed = new Set();
          S.preview = null; S.ov.hyp = new Set();
        } else if (res.outcome === "needs_acknowledgement") {
          S.dockMsg = { kind: "bad", text: res.reason };
        } else {
          S.dockMsg = { kind: "warn", text: res.reason, stale: !!res.stale };
        }
        break;
      case "restore_service":
        if (res.outcome === "restored") {
          S.restored = res; S.inc.state = "restored"; S.restoreArmed = false;
          S.ov = freshOverlay(); S.ov.breakPipe = S.inc.pipe || null;
        } else {
          S.dockMsg = { kind: "warn", text: res.reason };
        }
        break;
    }
  }
  if (!r.steps.length && /not issued|no plan|no longer/i.test(r.reply)) S.dockMsg = { kind: "warn", text: r.reply, stale: /no longer/i.test(r.reply) };
  S.proposal = r.proposal;
}

// ------------------------------------------------------------------ log
function kindOf(r) {
  const tools = r.steps.map((s) => s.tool);
  const outcomes = r.steps.map((s) => s.result.outcome);
  if (r.mode === "human-action") return outcomes.includes("issued") || outcomes.includes("restored") ? "human" : "warn";
  if (tools.includes("report_valve_condition")) return "observation";
  if (tools.includes("plan_isolation") && outcomes.includes("planned")) return "plan";
  if (outcomes.some((o) => ["not_in_graph", "ambiguous", "refused", "invalid_arguments"].includes(o))) return "warn";
  return "info";
}

function labelOf(r) {
  if (r.mode === "human-action") return "Supervisor action";
  if (r.steps.some((s) => s.tool === "report_valve_condition")) return "Crew observation saved";
  return r.mode === "llm" ? "ShutOff (live model)" : "ShutOff";
}

function headlineFor(r) {
  const by = (t) => r.steps.find((s) => s.tool === t);
  const plan = by("plan_isolation"), loc = by("locate_break"), chk = by("check_closure_plan"), iss = by("issue_work_order");
  const rep = by("report_valve_condition"), vs = by("valve_status"), sup = by("supply_check"), rst = by("restore_service");
  const rank = by("valve_exercise_priorities");
  if (iss) {
    const x = iss.result;
    if (x.outcome === "issued") return `${x.work_order} recorded · ${x.close_sequence.length} valves marked closed in the graph`;
    return "Not issued · " + (x.outcome === "needs_acknowledgement" ? "critical customer must be acknowledged" : x.reason);
  }
  if (rst) return rst.result.outcome === "restored" ? `${rst.result.work_order} restored · ${rst.result.reopened.length} valves re-opened` : "Not restored · " + rst.result.reason;
  if (plan && plan.result.outcome === "planned") {
    const k = kpisOf(plan.result);
    const pre = rep ? `${rep.result.valve} saved as ${rep.result.condition}. ` : "";
    return `${pre}Plan ${r.proposal ? r.proposal.id : ""} ready · ${k.valves} valves · ${k.accounts} accounts` + (k.critical ? ` · ${k.critical} critical` : "");
  }
  if (loc && loc.result.outcome === "ambiguous") return `Location ambiguous · ${loc.result.candidates.length} candidate mains`;
  if (loc && loc.result.outcome === "not_in_graph") return "Not in the network graph · " + loc.result.reason;
  if (chk) return chk.result.simulation.isolated ? "Crew plan would disconnect the break (simulation)" : "Crew plan rejected · the graph still connects the break to a source";
  if (vs) return r.reply;
  if (sup) return sup.result.outcome === "supplied" ? `${sup.result.customer}: source path exists (current graph)` : `${sup.result.customer}: no source path (current graph)`;
  if (rank) return `Valve exercise ranking · top ${rank.result.ranked[0].valve} (${rank.result.valves_evaluated} scenarios simulated)`;
  if (rep) return `${rep.result.valve} saved as ${rep.result.condition}`;
  return r.reply.split(/(?<=\.)\s/)[0];
}

function addEntry(kind, label, headline, r) {
  const li = document.createElement("li");
  li.className = "entry " + kind;
  let html = `<div class="k"><span>${esc(label)}</span><span>${clock()}</span></div><div class="h">${esc(headline)}</div>`;
  if (r) {
    const nq = r.steps.reduce((a, s) => a + s.queries.length, 0);
    const tags = [];
    if (r.steps.length) tags.push(`${r.steps.length} tool call${r.steps.length > 1 ? "s" : ""} · ${nq} graph queries`);
    if (r.mode === "llm") tags.push(r.verified ? "IDs and counts checked against graph results" : "model reply replaced by graph summary");
    if (r.note) tags.push(r.note);
    html += `<details><summary>Details</summary><p>${esc(r.reply)}</p>${tags.length ? `<div class="tags">${tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}</details>`;
  }
  li.innerHTML = html;
  $("#log").appendChild(li);
  $("#log").scrollTop = $("#log").scrollHeight;
}

// ------------------------------------------------------------------ strip
function renderStrip() {
  const chip = $("#state-chip"), title = $("#strip-title"), sub = $("#strip-sub");
  const inc = S.inc;
  const states = {
    idle: ["idle", "No incident"], locate: ["attention", "Location needed"], working: ["working", "Working"],
    ready: ["ready", "Simulation ready"], ack: ["ack", "Needs acknowledgement"], cannot: ["ack", "Cannot isolate"],
    issued: ["issued", "Work order recorded"], restored: ["idle", "Service restored"],
  };
  const [ds, label] = states[inc.state] || states.idle;
  chip.dataset.state = ds; chip.textContent = label;
  if (inc.state === "idle") {
    title.textContent = "Report a main break to start";
    sub.textContent = "ShutOff locates the main, works out which valves isolate it, and simulates the impact on a copy of the graph before anything changes.";
  } else if (inc.state === "locate") {
    title.textContent = "Location not resolved";
    sub.textContent = (inc.locate && inc.locate.reason) || "Give the street and the two cross streets.";
  } else {
    title.innerHTML = `${esc(inc.label || "")}<span class="mono">${esc(inc.pipe || "")}</span>`;
    const bits = [];
    if (inc.state === "issued" && S.issued) bits.push(`${S.issued.work_order} recorded · valves closed in the graph · crew dispatch and notices are simulated`);
    else if (inc.state === "restored" && S.restored) bits.push(`${S.restored.work_order} restored`);
    else if (S.proposal) bits.push(`Proposed plan ${S.proposal.id} · simulated, not yet approved`);
    if (S.observation) bits.push(`Crew observation: ${S.observation.valve} ${S.observation.condition} (${S.observation.at})`);
    sub.textContent = bits.join(" · ");
  }
  const show = S.plan && ["ready", "ack", "cannot", "issued"].includes(inc.state);
  $("#kpis").hidden = !show;
  if (show) {
    const k = kpisOf(S.plan), p = S.prevKpis;
    setKpi("valves", k.valves, p && p.valves);
    setKpi("accounts", k.accounts, p && p.accounts);
    setKpi("critical", k.critical, p && p.critical);
    $("#kpi-critical").classList.toggle("bad", k.critical > 0);
  }
  $("#fit-incident").disabled = !S.ov.breakPipe;
}

function setKpi(name, val, prev) {
  const b = $("#k-" + name), d = $("#d-" + name);
  const changed = b.textContent !== String(val) && b.textContent !== "–";
  b.textContent = val;
  if (prev == null || prev === val) { d.textContent = ""; d.className = "delta"; }
  else { const diff = val - prev; d.textContent = (diff > 0 ? "+" : "") + diff; d.className = "delta " + (diff > 0 ? "up" : "down"); }
  if (changed && !reduceMotion()) { const k = b.closest(".kpi"); k.classList.remove("flash"); void k.offsetWidth; k.classList.add("flash"); }
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

const width = (p) => { const d = p ? p.diameter_mm : 150; return d >= 400 ? 7 : d >= 200 ? 5 : d >= 150 ? 4 : 3; };
const GLYPH = { hospital: ["H", "Hospital"], care_home: ["C", "Care home"], medical_priority: ["D", "Dialysis account"], school: ["S", "School"] };

function renderMap() {
  const svg = $("#map"), net = S.net, by = S.byId, ov = S.ov;
  if (!net) return;
  svg.innerHTML = "";
  svg.setAttribute("viewBox", S.view.join(" "));
  const dry = new Set(net.dry_pipes);
  const g = (name) => el("g", { class: name }, svg);
  const gStreets = g("streets"), gEdges = g("edges"), gHit = g("hits"), gTrace = g("traces"), gNodes = g("nodes"), gCust = g("cust"), gTop = g("top");

  for (const n of net.nodes) {
    if (n.kind !== "Junction" || !/^J-\d\d$/.test(n.id)) continue;
    const [street, ave] = n.name.split(" & ");
    if (n.id.endsWith("0")) el("text", { x: n.x + 85, y: n.y - 10, class: "street", "text-anchor": "middle" }, gStreets).textContent = street.toUpperCase();
    if (n.id[2] === "0") el("text", { x: n.x, y: n.y - 32, class: "street", "text-anchor": "middle" }, gStreets).textContent = ave.toUpperCase();
  }
  if (by["J-WE"]) el("text", { x: by["J-WE"].x + 12, y: by["J-WE"].y + 5, class: "street" }, gStreets).textContent = "WILLOW CT";

  for (const e of net.edges) {
    const a = by[e.a], b = by[e.b];
    if (!a || !b) continue;
    const pid = pipeOf(a, b);
    let cls = "edge";
    if (ov.proposed.has(pid)) cls += " proposed reveal";
    else if (ov.loss.has(pid)) cls += " loss reveal";
    else if (dry.has(pid)) cls += " out";
    el("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: cls, "stroke-width": width(by[pid]) }, gEdges);
  }

  const custByPipe = {};
  for (const c of net.customers) (custByPipe[c.pipe] ||= []).push(c);
  for (const p of net.nodes.filter((n) => n.kind === "Pipe")) {
    const hit = el("line", { x1: p.ax, y1: p.ay, x2: p.bx, y2: p.by, class: "hit" }, gHit);
    const cs = custByPipe[p.id] || [];
    const accts = cs.reduce((a, c) => a + c.accounts, 0);
    hover(hit, () => `<b>${esc(p.label)}</b><span class="mono">${esc(p.id)}</span> · ${esc(p.material)}, ${esc(p.installed)} · ${accts} accounts`);
  }

  for (const t of ov.paths) {
    const pts = t.path.map((id) => by[id]).filter(Boolean);
    if (pts.length < 2) continue;
    const pl = el("polyline", { points: pts.map((n) => `${n.x},${n.y}`).join(" "), class: `trace ${t.kind}` }, gTrace);
    if (!reduceMotion()) {
      const len = Math.ceil(pl.getTotalLength());
      pl.style.setProperty("--len", len); pl.style.strokeDasharray = len; pl.classList.add("draw");
    }
  }

  for (const n of net.nodes) {
    if (n.kind === "Junction") el("circle", { cx: n.x, cy: n.y, r: 3.5, class: "junction" }, gNodes);
    if (n.kind === "Source") {
      if (n.source_kind === "reservoir") el("rect", { x: n.x - 24, y: n.y - 17, width: 48, height: 34, rx: 7, class: "source-shape" }, gNodes);
      else el("circle", { cx: n.x, cy: n.y, r: 21, class: "source-shape" }, gNodes);
      el("text", { x: n.x, y: n.y + (n.source_kind === "reservoir" ? 38 : 42), class: "source-label", "text-anchor": "middle" }, gNodes).textContent = n.name;
    }
    if (n.kind === "Valve") drawValve(n, gNodes, gTop);
  }

  for (const c of net.customers) {
    if (!GLYPH[c.kind]) continue;
    const p = by[c.pipe];
    if (!p) continue;
    const horiz = Math.abs(p.ay - p.by) < 1;
    const cx = horiz ? p.x + 22 : p.x + 20, cy = horiz ? p.y - 20 : p.y + 16;
    let st = "";
    if (c.id in ov.critical) st = ov.critical[c.id] ? "ok" : "out";
    if (dry.has(c.pipe) || ov.proposed.has(c.pipe) || ov.loss.has(c.pipe)) st = "out";
    const gg = el("g", { class: `cust focusable ${st} ${S.focus === c.id ? "selected" : ""}`, tabindex: 0, role: "button",
      "aria-label": `${c.name}${st === "out" ? ", would lose supply" : st === "ok" ? ", keeps a source path" : ""}` }, gCust);
    el("circle", { cx, cy, r: 18, class: "ring" }, gg);
    el("circle", { cx, cy, r: 12, class: `cust-dot ${st}` }, gg);
    el("text", { x: cx, y: cy, class: "cust-glyph" }, gg).textContent = GLYPH[c.kind][0];
    el("text", { x: cx + 17, y: cy + 5, class: "cust-name" }, gg).textContent = GLYPH[c.kind][1];
    const status = st === "out" ? "Would lose its source connection" : st === "ok" ? "Keeps a source path in the simulation" : "In service";
    hover(gg, () => `<b>${esc(c.name)}</b>${c.critical ? "Critical customer" : "Sensitive customer"} · ${status}`);
    gg.addEventListener("click", () => selectCustomer(c.id));
    gg.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectCustomer(c.id); } });
  }

  if (ov.breakPipe && by[ov.breakPipe]) {
    const p = by[ov.breakPipe];
    if (!reduceMotion()) el("circle", { cx: p.x, cy: p.y, r: 9, class: "break-ring" }, gTop);
    el("circle", { cx: p.x, cy: p.y, r: 6.5, class: "break-dot" }, gTop);
    el("text", { x: p.x, y: p.y + 27, class: "break-label", "text-anchor": "middle" }, gTop).textContent = "BREAK";
  }
  $("#clear-focus").hidden = !(S.focus || S.preview || ov.paths.length || ov.marked.size);
}

function drawValve(n, gNodes, gTop) {
  const ov = S.ov;
  let cls = "valve";
  if (n.condition === "unknown") cls += " unknown";
  if (n.seized) cls += " seized";
  else if (n.status === "closed") cls += " closed";
  else if (ov.planValves.has(n.id)) cls += " proposed";
  const interesting = ov.planValves.has(n.id) || n.seized || ov.hyp.has(n.id) || ov.marked.has(n.id) || S.focus === n.id;
  const gv = el("g", { class: `focusable ${S.focus === n.id ? "selected" : ""}`, ...(interesting ? { tabindex: 0, role: "button", "aria-label": `Valve ${n.id}, ${n.location}` } : {}) }, interesting ? gTop : gNodes);
  el("circle", { cx: n.x, cy: n.y, r: 12, class: "ring" }, gv);
  el("rect", { x: n.x - 5.5, y: n.y - 5.5, width: 11, height: 11, rx: 2, class: cls }, gv);
  if (n.seized) el("path", { d: `M${n.x - 3} ${n.y - 3}L${n.x + 3} ${n.y + 3}M${n.x + 3} ${n.y - 3}L${n.x - 3} ${n.y + 3}`, class: "vx" }, gv);
  if (ov.hyp.has(n.id)) el("circle", { cx: n.x, cy: n.y, r: 10, class: "hyp-ring" }, gv);
  if (ov.marked.has(n.id)) el("circle", { cx: n.x, cy: n.y, r: 10, class: "hyp-ring", style: "stroke: var(--water)" }, gv);
  if (ov.changed.has(n.id) && !reduceMotion()) el("circle", { cx: n.x, cy: n.y, r: 6, class: "changed", fill: "none" }, gv);
  if (interesting) {
    const lc = n.seized ? "seized" : ov.planValves.has(n.id) ? "proposed" : "";
    el("text", { x: n.x + 9, y: n.y - 9, class: `vlabel ${lc}` }, gv).textContent = n.id;
  }
  const what = n.seized ? "Reported seized (crew observation)" : n.status === "closed" ? "Closed in the graph (work order)" : ov.planValves.has(n.id) ? "Proposed to close" : "Open";
  hover(gv, () => `<b>${esc(n.id)} · ${esc(n.location)}</b>${what} · condition ${esc(n.condition)} · last exercised ${esc(n.last_exercised)}`);
  gv.addEventListener("click", () => selectValve(n.id));
  gv.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectValve(n.id); } });
}

function hover(node, html) {
  const show = () => {
    const info = $("#info"), box = $("#mapbox").getBoundingClientRect(), r = node.getBoundingClientRect();
    info.innerHTML = html();
    info.hidden = false;
    let x = r.right - box.left + 10, y = r.top - box.top - 4;
    if (x + 290 > box.width) x = r.left - box.left - 290;
    info.style.left = Math.max(6, x) + "px"; info.style.top = Math.max(6, Math.min(y, box.height - 70)) + "px";
  };
  const hide = () => { $("#info").hidden = true; };
  node.addEventListener("mouseenter", show); node.addEventListener("mouseleave", hide);
  node.addEventListener("focus", show); node.addEventListener("blur", hide);
}

function selectValve(id) {
  S.focus = S.focus === id ? null : id;
  renderMap(); renderRail();
}

function selectCustomer(id) {
  S.focus = S.focus === id ? null : id;
  const st = S.plan && S.plan.simulation.critical_status.find((c) => c.id === id);
  S.ov.paths = S.focus && st && st.path ? [{ path: st.path.path, kind: "supply" }] : [];
  renderMap(); renderRail();
}

function setPreview(cont) {
  if (!cont || (S.preview && S.preview.if_fails === cont.if_fails)) { S.preview = null; S.ov.hyp = new Set(); $("#preview-tag").hidden = true; }
  else {
    S.preview = cont;
    S.ov.hyp = new Set(cont.close_instead);
    $("#preview-tag").textContent = `Preview: if ${cont.if_fails} cannot close · hypothetical, nothing recorded`;
    $("#preview-tag").hidden = false;
  }
  renderMap(); renderRail();
}

// viewBox animation for fit controls
function animateView(target) {
  const from = S.view.slice();
  if (reduceMotion()) { S.view = target; $("#map").setAttribute("viewBox", target.join(" ")); return; }
  const t0 = performance.now(), dur = 260;
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    S.view = from.map((v, i) => v + (target[i] - v) * e);
    $("#map").setAttribute("viewBox", S.view.join(" "));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function fitIncident() {
  const by = S.byId, ov = S.ov, pts = [];
  const addPipe = (id) => { const p = by[id]; if (p) pts.push([p.ax, p.ay], [p.bx, p.by]); };
  if (ov.breakPipe) addPipe(ov.breakPipe);
  ov.proposed.forEach(addPipe); ov.loss.forEach(addPipe);
  for (const v of [...ov.planValves, ...ov.hyp]) if (by[v]) pts.push([by[v].x, by[v].y]);
  if (!pts.length) return;
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [x, y] of pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
  const pad = 110, box = $("#mapbox").getBoundingClientRect(), aspect = box.width / Math.max(box.height, 1);
  let w = x1 - x0 + 2 * pad, h = y1 - y0 + 2 * pad;
  if (w / h < aspect) w = h * aspect; else h = w / aspect;
  animateView([(x0 + x1) / 2 - w / 2, (y0 + y1) / 2 - h / 2, w, h]);
}

// ------------------------------------------------------------------ rail
const ICON = {
  ok: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="9" fill="currentColor" opacity=".15"/><path d="M5.5 10.5l3 3 6-7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  bad: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="9" fill="currentColor" opacity=".15"/><path d="M10 5.5v5.5M10 14v.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
  info: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="9" fill="currentColor" opacity=".15"/><path d="M10 9v5M10 6v.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
};
const verdict = (kind, title, small = "") => `<div class="verdict ${kind}">${ICON[kind === "good" ? "ok" : kind === "info" ? "info" : "bad"]}<div class="t">${title}${small ? `<small>${small}</small>` : ""}</div></div>`;

function renderRail() {
  const box = $("#plan"), inc = S.inc;
  let h = "";
  if (inc.state === "idle" || (!S.plan && inc.state !== "locate")) {
    h = `<div class="empty-state"><h2>No active incident</h2>
      <p>Report where a main has broken. ShutOff will:</p>
      <ol><li>find the smallest area valves can shut off (graph components),</li><li>simulate the closures on a copy of the graph and show who loses water,</li><li>predict what happens if a valve will not turn,</li><li>wait for your approval before recording anything.</li></ol>
      <button class="primary" type="button" data-msg="Caller reports water gushing on Maple St between 2nd and 3rd Ave">Report a break on Maple St</button>
      <p class="note">Simulated network for demonstration. Connectivity only: no pressure or flow modelling.</p></div>`;
    if (S.ranking) h += rankingHtml();
  } else if (inc.state === "locate") {
    const l = inc.locate || {};
    h = `<div class="sec">${verdict("warn", l.outcome === "ambiguous" ? "More than one main matches this report" : "This location is not in the network graph", esc(l.reason || ""))}`;
    if (l.candidates && l.candidates.length) {
      h += `<div class="cand">` + l.candidates.map((c) => `<button type="button" data-msg="${esc(candidateText(c.label))}">${esc(c.label)} <span class="muted mono">${esc(c.pipe)}</span></button>`).join("") + `</div>`;
    }
    h += `<p class="note">Nothing can be approved until the break is located.</p></div>`;
  } else {
    h = planHtml();
  }
  box.innerHTML = h;
  wireRail(box);
  renderDock();
}

function candidateText(label) {
  const m = /^(.*?), (.*?) to (.*)$/.exec(label);
  return m ? `Break on ${m[1]} between ${m[2]} and ${m[3]}` : label;
}

function planHtml() {
  const p = S.plan, sim = p.simulation, issued = S.inc.state === "issued" || S.inc.state === "restored";
  let h = "";
  const restored = S.inc.state === "restored";
  h += `<div class="sec">${!sim.isolated
    ? verdict("bad", "No combination of operable valves isolates this break", "Field crews must find another isolation point.")
    : restored ? verdict("info", "Service restored", "The plan below is shown for reference only.")
    : issued ? verdict("good", "Approved plan, recorded in the graph", "The work order marked these valves closed. Dispatch and notices are simulated.")
    : verdict("good", "In simulation the break would be disconnected from every source", "Computed on a copy of the graph: proposed valves removed, connectivity re-checked. Nothing has changed yet.")}</div>`;

  // the single most important risk, surfaced before the details
  if (!issued && sim.isolated) {
    const crit0 = new Set(sim.critical_out.map((c) => c.name));
    const worst = p.contingencies.map((c) => ({ ...c, newCrit: c.critical_out.filter((n) => !crit0.has(n)) }))
      .filter((c) => c.newCrit.length || !c.isolated).sort((a, b) => risk(b) - risk(a))[0];
    if (worst) {
      const v = p.close.find((x) => x.id === worst.if_fails);
      h += `<div class="sec">${verdict("warn", `Risk to watch: ${esc(worst.if_fails)}${v && v.condition !== "ok" ? ` (${esc(v.condition)}, last exercised ${esc(v.last_exercised)})` : ""}`,
        !worst.isolated ? "If it cannot close, no operable valves would isolate the break."
          : `If it cannot close, the shutdown grows to ${worst.close_instead.length} valves and ${worst.accounts_out} accounts and cuts off ${esc(worst.newCrit.map(shortName).join(", "))}.`)}
        <button type="button" class="link-btn" data-cont="${esc(worst.if_fails)}" style="margin-top:6px">${S.preview && S.preview.if_fails === worst.if_fails ? "Hide preview" : "Preview on map (hypothetical)"}</button></div>`;
    }
  }

  // critical customers
  h += `<div class="sec"><h3>Priority customers <span class="muted">simulation</span></h3><ul class="rows">` +
    sim.critical_status.map((c) => {
      const out = !c.supplied;
      const sub = out ? (c.already_out ? "Already out under another work order" : "Would lose its source connection under this plan")
        : `Keeps a source path · ${c.path ? `${c.path.hops} hops from ${esc(c.path.source)}` : ""}`;
      return `<li><button type="button" class="row ${S.focus === c.id ? "selected" : ""}" data-cust="${esc(c.id)}"><span class="dot ${out ? "out" : "ok"}"></span><span class="grow">${esc(c.name)}<span class="sub">${sub}</span></span>${out ? `<span class="badge bad">cut off</span>` : `<span class="link-btn">path</span>`}</button></li>`;
    }).join("") + `</ul></div>`;

  // valves
  h += `<div class="sec"><h3>Valves to close <span class="muted">${p.close.length}</span></h3><ul class="rows">` +
    p.close.map((v) => `<li><button type="button" class="row ${S.focus === v.id ? "selected" : ""}" data-valve="${esc(v.id)}"><span class="id">${esc(v.id)}</span><span class="grow">${esc(v.location)}</span>${v.condition !== "ok" ? `<span class="badge warn">${esc(v.condition)} · ${esc(v.last_exercised)}</span>` : ""}${S.ov.changed.has(v.id) ? `<span class="badge hyp">new</span>` : ""}</button></li>`).join("") +
    `</ul><p class="note">Listed by ID. ShutOff derives which valves bound the area; it does not plan an operating sequence.</p></div>`;

  // affected
  const intended = sim.intended.reduce((a, c) => a + c.accounts, 0), unintended = sim.unintended.reduce((a, c) => a + c.accounts, 0);
  h += `<div class="sec"><h3>Who would lose water</h3><div class="split">
    <div class="stat"><b>${intended}</b><span>accounts inside the shut-off area</span></div>
    <div class="stat ${unintended ? "bad" : ""}"><b>${unintended}</b><span>accounts outside it (only fed through it)</span></div></div>` +
    (sim.unintended.length ? `<p class="note">Outside the area: ${esc(sim.unintended.map((c) => c.name).join(", "))}.</p>` : "") +
    (sim.already_out_accounts ? `<p class="note">${sim.already_out_accounts} accounts are already out under other work orders and are not counted here.</p>` : "") + `</div>`;

  // crew check
  if (S.check) {
    const c = S.check.simulation;
    h += `<div class="sec"><h3>Crew's alternative</h3>` + (c.isolated
      ? verdict("good", `Closing ${esc(S.check.proposed.join(" + "))} would also disconnect the break`, `${c.accounts_out} accounts would lose water.`)
      : verdict("bad", `Closing only ${esc(S.check.proposed.join(" + "))} leaves the break connected`, `The graph still links it to ${esc(c.leak_path ? c.leak_path.source : "a source")}${c.leak_path ? ` in ${c.leak_path.hops} hops` : ""} (purple on the map).`)) + `</div>`;
  }

  // contingencies
  if (p.contingencies.length && !issued) {
    const crit0 = new Set(sim.critical_out.map((c) => c.name));
    const rows = p.contingencies.map((c) => ({ ...c, newCrit: c.critical_out.filter((n) => !crit0.has(n)) }))
      .sort((a, b) => risk(b) - risk(a));
    h += `<div class="sec"><h3>If a valve cannot close <span class="muted">hypothetical</span></h3><ul class="rows">` +
      rows.map((c, i) => {
        const sub = !c.isolated ? "No backup isolation with operable valves"
          : `${c.close_instead.length} valves · ${c.accounts_out} accounts${c.extra_accounts ? ` (+${c.extra_accounts})` : ""}`;
        const sel = S.preview && S.preview.if_fails === c.if_fails;
        return `<li><button type="button" class="row ${sel ? "selected" : ""}" data-cont="${esc(c.if_fails)}"><span class="id">${esc(c.if_fails)}</span><span class="grow">${sel ? "Previewing on map" : "Preview on map"}<span class="sub">${sub}</span></span>${c.newCrit.length ? `<span class="badge bad">+ ${esc(c.newCrit.map(shortName).join(", "))}</span>` : i === 0 && c.extra_accounts ? `<span class="badge warn">highest risk</span>` : ""}</button></li>`;
      }).join("") + `</ul><p class="note">Pre-computed by re-running the graph analysis with each valve stuck open. Previewing records nothing.</p></div>`;
  }

  // history + evidence
  if (p.history && p.history.length) {
    h += `<div class="sec"><h3>This main</h3><p class="note" style="margin:0">${esc(p.pipe.material)}, installed ${esc(p.pipe.installed)} · ${p.history.length} earlier break${p.history.length === 1 ? "" : "s"} recorded (${esc(p.history.map((x) => x.at).join(", "))}, synthetic history)</p></div>`;
  }
  h += evidenceHtml();
  return h;
}

const risk = (c) => c.newCrit.length * 1000 + (c.isolated ? 0 : 500) + c.extra_accounts;
const shortName = (n) => /dialysis/i.test(n) ? "dialysis account" : /hospital/i.test(n) ? "hospital" : /senior|care/i.test(n) ? "care home" : n;

function evidenceHtml() {
  const p = S.plan, sim = p.simulation;
  const queries = S.steps.reduce((a, s) => a + s.queries.length, 0);
  const tools = S.steps.slice(-12).reverse().map((st) => `<details><summary>${esc(st.tool)} · ${st.queries.length} queries · ${st.ms} ms</summary>` +
    st.queries.slice(0, 12).map((q) => `<div class="q"><div class="qh"><b>${esc(q.name)}</b><span>${/__sim|sandbox/.test(q.graph) ? "copy · " : ""}${q.rows} rows · ${q.ms} ms</span></div><pre>${esc(q.cypher)}</pre></div>`).join("") +
    (st.queries.length > 12 ? `<p class="note">${st.queries.length - 12} more (backup-plan simulations)</p>` : "") + `</details>`).join("");
  return `<div class="sec evidence"><details><summary><b>Graph evidence</b> · why these valves</summary><ul>
    <li><b>Isolation area</b>: <span class="mono">algo.WCC</span> over junctions and pipes, with operable valves as walls → ${esc(p.segment.members.join(", "))}.</li>
    <li><b>Valves to close</b>: valves connected to that area → ${p.close.length}.</li>
    <li><b>Impact</b>: <span class="mono">GRAPH.COPY</span> of the network, proposed valves removed, WCC again → pipes with no source: ${esc(sim.dry_pipes.join(", ") || "none")}.</li>
    <li><b>Proof paths</b>: <span class="mono">algo.SPpaths</span> from each source to each priority customer on the copy.</li>
    <li><b>Backup plans</b>: ${p.contingencies.length} more simulations, one per valve.</li></ul>
    <p class="note">Paths show graph connectivity, not hydraulic flow or pressure.</p>
    <details><summary>All queries for this incident (${queries})</summary><div class="trace-list">${tools}</div></details></details></div>`;
}

function rankingHtml() {
  const r = S.ranking;
  return `<div class="sec"><h3>Valves to exercise first <span class="muted">${r.valves_evaluated} simulated</span></h3><ul class="rows">` +
    r.ranked.slice(0, 6).map((x) => `<li><button type="button" class="row" data-valve="${esc(x.valve)}"><span class="id">${esc(x.valve)}</span><span class="grow">${esc(x.location)}<span class="sub">If seized: ${x.accounts_out_if_seized} accounts${x.critical_out_if_seized.length ? " · " + esc(x.critical_out_if_seized.map(shortName).join(", ")) : ""} · last exercised ${esc(x.last_exercised)}</span></span></button></li>`).join("") +
    `</ul><p class="note">Heuristic score: accounts + 60 per critical customer, weighted by condition and age. Not a calibrated risk model.</p></div>`;
}

function wireRail(box) {
  for (const b of box.querySelectorAll("[data-valve]")) b.onclick = () => selectValve(b.dataset.valve);
  for (const b of box.querySelectorAll("[data-cust]")) b.onclick = () => selectCustomer(b.dataset.cust);
  for (const b of box.querySelectorAll("[data-cont]")) b.onclick = () => setPreview(S.plan.contingencies.find((c) => c.if_fails === b.dataset.cont));
  for (const b of box.querySelectorAll("[data-msg]")) b.onclick = () => send(b.dataset.msg);
}

// ------------------------------------------------------------------ dock (approval always next to its context)
function renderDock() {
  const dock = $("#dock"), inc = S.inc, p = S.plan;
  let h = "";
  const msg = S.dockMsg ? verdict(S.dockMsg.kind === "bad" ? "bad" : "warn", esc(S.dockMsg.text)) : "";
  if ((inc.state === "ready" || inc.state === "ack") && S.proposal && p) {
    const crit = p.simulation.critical_out;
    h += msg;
    h += `<div class="dock-summary"><b>${esc(p.pipe.label)}</b> · plan <span class="mono">${esc(S.proposal.id)}</span><br>${p.close.length} valves · ${p.simulation.accounts_out} accounts would lose water` +
      (crit.length ? ` · <b style="color:var(--loss)">${crit.length} critical</b>` : " · no critical customer") + `</div>`;
    if (crit.length) h += `<label class="ack"><input type="checkbox" id="ack"><span>I acknowledge that <b>${esc(crit.map((c) => c.name).join(", "))}</b> would lose its water supply, and that a priority notice and alternate supply are needed.</span></label>`;
    h += `<button type="button" class="primary" id="approve" ${crit.length ? "disabled" : ""}>Approve simulated work order</button>`;
    if (S.dockMsg && S.dockMsg.stale) h += `<button type="button" class="secondary" id="replan">Re-plan with the current network</button>`;
    h += `<div class="dock-note">The server re-checks the plan before recording it. Recording marks valves closed in the graph; no crew is dispatched and no message is sent.</div>`;
  } else if (inc.state === "issued" && S.issued) {
    const w = S.issued;
    h += verdict("good", `${esc(w.work_order)} recorded in the graph`, `${w.close_sequence.length} valves marked closed · ${w.standard_notices} standard notices` + (w.priority_notices.length ? ` · priority notice for ${esc(w.priority_notices.map(shortName).join(", "))}` : "") + " recorded. Dispatch and notices are simulated.");
    h += msg;
    h += `<button type="button" class="secondary danger" id="restore">${S.restoreArmed ? `Confirm: restore ${esc(w.work_order)}` : "Restore service"}</button>`;
    h += `<div class="dock-note">Restore re-opens valves only after checking on a copy of the graph that no other active break would be re-fed.</div>`;
  } else if (inc.state === "restored" && S.restored) {
    const r = S.restored;
    h += verdict("info", `${esc(r.work_order)} restored`, `Re-opened ${esc(r.reopened.join(", ") || "none")}` + (r.kept_closed.length ? `; kept ${esc(r.kept_closed.map((k) => k.valve).join(", "))} closed for another active break` : "") + ".");
  } else if (inc.state === "cannot") {
    h += verdict("bad", "Nothing to approve", "The graph shows no isolation with operable valves.");
  } else if (msg) {
    h += msg;
  }
  dock.innerHTML = h;
  dock.hidden = !h;
  const ack = $("#ack"), ap = $("#approve");
  if (ack && ap) ack.onchange = () => { ap.disabled = !ack.checked || S.busy; };
  if (ap) ap.onclick = () => {
    const ids = ack && ack.checked ? p.simulation.critical_out.map((c) => c.id) : [];
    send("", "/api/approve", { acknowledge: ids, proposal_id: S.proposal.id }, ids.length ? "Supervisor approved (critical impact acknowledged)" : "Supervisor approved");
  };
  const rp = $("#replan");
  if (rp) rp.onclick = () => send("", "/api/replan", {}, "Supervisor asked to re-plan");
  const rs = $("#restore");
  if (rs) rs.onclick = () => {
    if (!S.restoreArmed) { S.restoreArmed = true; renderDock(); setTimeout(() => { if (S.restoreArmed) { S.restoreArmed = false; renderDock(); } }, 5000); return; }
    send("", "/api/restore", { work_order: S.issued.work_order }, `Supervisor restored ${S.issued.work_order}`);
  };
}

function renderWO() {
  const bar = $("#wo-bar"), wos = (S.net && S.net.work_orders) || [];
  bar.hidden = !wos.length;
  bar.innerHTML = wos.map((w) => `<span class="wo-chip"><b>${esc(w.id)}</b> ${esc(w.pipe_label)} · ${w.valves.length} valves closed · ${w.accounts_out} accounts</span>`).join("");
}

function renderAll() { renderStrip(); renderMap(); renderRail(); renderWO(); }

// ------------------------------------------------------------------ wiring
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const t = $("#input").value.trim();
  if (!t) return;
  $("#input").value = "";
  send(t);
});
for (const b of document.querySelectorAll(".starter [data-msg]")) b.onclick = () => send(b.dataset.msg);
$("#fit-network").onclick = () => animateView(FULL_VIEW.slice());
$("#fit-incident").onclick = fitIncident;
$("#clear-focus").onclick = () => { S.focus = null; S.ov.paths = []; S.ov.marked = new Set(); setPreview(null); };
$("#theme-btn").onclick = () => {
  const t = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("shutoff-theme", t); } catch (e) {}
  $("#theme-btn").setAttribute("aria-label", `Switch to ${t === "dark" ? "light" : "dark"} theme`);
};
$("#log-toggle").onclick = () => {
  const open = $("#workspace").classList.toggle("log-open");
  $("#log-toggle").setAttribute("aria-expanded", String(open));
};
for (const b of document.querySelectorAll("#tabs button")) b.onclick = () => {
  $("#workspace").dataset.view = b.dataset.view;
  for (const x of document.querySelectorAll("#tabs button")) x.setAttribute("aria-pressed", String(x === b));
};
$("#reset-btn").onclick = async () => {
  if (S.busy) return;
  setBusy(true);
  try {
    await api("/api/reset", {});
    Object.assign(S, { inc: { state: "idle" }, plan: null, prevKpis: null, check: null, observation: null, issued: null, restored: null,
      dockMsg: null, ranking: null, proposal: null, steps: [], ov: freshOverlay(), preview: null, focus: null,
      session: "op-" + Math.random().toString(36).slice(2, 8) });
    $("#log").innerHTML = ""; $("#preview-tag").hidden = true;
    addEntry("info", "Demo", "Synthetic network reloaded into FalkorDB; field reports and work orders cleared.", null);
    await loadNetwork();
  } finally { setBusy(false); renderAll(); }
};

(async function init() {
  $("#theme-btn").setAttribute("aria-label", `Switch to ${document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"} theme`);
  const ok = await loadHealth();
  try {
    await loadNetwork();
    addEntry("info", "ShutOff", "Watching the simulated Riverbend network. Report a break to begin.", null);
  } catch (e) {
    addEntry("warn", "Error", "Cannot load the network from the server" + (ok ? "." : ": FalkorDB or the API is not reachable."), null);
    S.dockMsg = { kind: "bad", text: "Start FalkorDB (docker compose up -d) and the API, then reload this page." };
  }
  renderAll();
})();
