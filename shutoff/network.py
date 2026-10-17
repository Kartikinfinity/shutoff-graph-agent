"""Isolation planning, simulation and actions over the FalkorDB graph.

Read paths use the live graph (read-only queries). "What would happen if we
closed these valves" runs on a sandbox: a GRAPH.COPY of the live network that
is thrown away afterwards. Only issue_work_order / report_valve /
restore_work_order write to the live graph, and issue_work_order re-runs the
simulation itself instead of trusting whoever called it.
"""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from datetime import datetime

from . import db as dbm
from . import queries as Q

ORDINALS = {"first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th", "sixth": "6th"}
SUFFIXES = {"street": "st", "st.": "st", "avenue": "ave", "ave.": "ave", "road": "rd", "rd.": "rd",
            "court": "ct", "ct.": "ct"}


def now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- resolution

def _norm(s: str) -> str:
    words = re.sub(r"[^a-z0-9 .]", " ", s.lower()).split()
    return " ".join(SUFFIXES.get(ORDINALS.get(w, w), ORDINALS.get(w, w)) for w in words)


def known_streets() -> list[str]:
    rows = dbm.run("street_names", Q.STREET_NAMES)
    return sorted(r["street"] for r in rows)


def resolve_street(text: str | None, streets: list[str] | None = None) -> str | None:
    """Map 'maple', 'Maple Street', '3rd', 'third avenue' to a street name that
    exists in the graph. Returns None rather than guessing."""
    if not text:
        return None
    streets = streets if streets is not None else known_streets() + ["Willow Ct"]
    t = _norm(text)
    exact = [s for s in streets if _norm(s) == t]
    if exact:
        return exact[0]
    starts = [s for s in streets if _norm(s).split()[0] == t.split()[0]]
    return starts[0] if len(starts) == 1 else None


def locate_break(street: str, cross_a: str | None = None, cross_b: str | None = None,
                 landmark: str | None = None) -> dict:
    if landmark and not street:
        hits = dbm.run("customer_search", Q.CUSTOMER_SEARCH, {"q": landmark.strip()[:60]})
        if not hits:
            return {"outcome": "not_in_graph", "reason": f"No customer or landmark matching '{landmark}'."}
        h = hits[0]
        return {"outcome": "found", "pipe": h["pipe"], "label": h["pipe_label"],
                "matched": f"main serving {h['name']}"}
    streets = known_streets()
    s = resolve_street(street, streets)
    if not s:
        return {"outcome": "not_in_graph", "reason": f"Street '{street}' is not in the network graph.",
                "known_streets": streets}
    a = resolve_street(cross_a, streets + ["Willow Ct"]) if cross_a else None
    b = resolve_street(cross_b, streets + ["Willow Ct"]) if cross_b else None
    if cross_a and not a:
        return {"outcome": "not_in_graph", "reason": f"Cross street '{cross_a}' is not in the network graph."}
    if cross_b and not b:
        return {"outcome": "not_in_graph", "reason": f"Cross street '{cross_b}' is not in the network graph."}
    if a and b:
        rows = dbm.run("pipe_between", Q.PIPE_BETWEEN, {"street": s, "a": a, "b": b})
        if len(rows) == 1:
            return {"outcome": "found", "pipe": rows[0]["id"], "label": rows[0]["label"]}
    if not a and not b:
        rows = dbm.run("pipes_on_street", Q.PIPES_ON_STREET, {"street": s})
        return {"outcome": "ambiguous", "reason": f"Which block of {s}? Give the two cross streets.",
                "candidates": [{"pipe": r["id"], "label": r["label"]} for r in rows]}
    rows = dbm.run("pipes_touching", Q.PIPES_ON_STREET_TOUCHING, {"street": s, "a": a or "", "b": b or ""})
    if not rows:
        return {"outcome": "not_in_graph", "reason": f"No main on {s} between those streets."}
    if len(rows) == 1:
        return {"outcome": "found", "pipe": rows[0]["id"], "label": rows[0]["label"]}
    return {"outcome": "ambiguous", "reason": "More than one main matches; which block?",
            "candidates": [{"pipe": r["id"], "label": r["label"]} for r in rows]}


def pipe_info(pipe_id: str) -> dict | None:
    rows = dbm.run("pipe_by_id", Q.PIPE_BY_ID, {"id": pipe_id})
    return rows[0] if rows else None


def valve_info(valve_id: str) -> dict | None:
    rows = dbm.run("valve_by_id", Q.VALVE_BY_ID, {"id": valve_id})
    return rows[0] if rows else None


# ---------------------------------------------------------------- isolation

def segment(pipe_id: str) -> dict:
    rows = dbm.run("segment_of_pipe", Q.SEGMENT_OF_PIPE, {"pipe": pipe_id})
    members = [r["id"] for r in rows]
    return {
        "members": members,
        "pipes": sorted(r["id"] for r in rows if r["kind"] == "Pipe"),
        "seized_valves": sorted(r["id"] for r in rows if r["kind"] == "Valve"),
    }


def boundary_valves(members: list[str]) -> list[dict]:
    return dbm.run("boundary_valves", Q.BOUNDARY_VALVES, {"members": members})


def customers_on(pipes: list[str]) -> list[dict]:
    if not pipes:
        return []
    return dbm.run("customers_on_pipes", Q.CUSTOMERS_ON_PIPES, {"pipes": pipes})


@contextmanager
def sandbox():
    """A throwaway GRAPH.COPY of the live network."""
    name = f"{dbm.GRAPH}__sim_{uuid.uuid4().hex[:8]}"
    t = dbm.current_trace()
    if t is not None:
        t.queries.append(dbm.QueryRecord("sandbox_copy", f"GRAPH.COPY {dbm.GRAPH} {name}", {}, 0, 0, dbm.GRAPH))
    dbm.graph().copy(name)
    try:
        yield name
    finally:
        try:
            dbm.graph(name).delete()
        except Exception:
            pass


def simulate(close: list[str], break_pipe: str | None = None, segment_pipes: list[str] | None = None) -> dict:
    """Close `close` (plus anything already closed) on a sandbox copy and report
    what happens: is the break still fed, who loses water, are critical
    customers still supplied (with the path that proves it)."""
    segment_pipes = segment_pipes or []
    with sandbox() as sb:
        dbm.run("sim_apply_closures", Q.SIM_APPLY_CLOSURES, {"close": close}, write=True, graph_name=sb)
        dry = {r["pipe"] for r in dbm.run("sim_dry_pipes", Q.SIM_DRY_PIPES, graph_name=sb)}
        leak_path = None
        if break_pipe and break_pipe not in dry:
            rows = dbm.run("sim_supply_path", Q.SIM_SUPPLY_PATH, {"target": break_pipe}, graph_name=sb)
            leak_path = rows[0] if rows else None
        critical = dbm.run("critical_customers", Q.CRITICAL_CUSTOMERS, graph_name=sb)
        critical_status = []
        for c in critical:
            if c["pipe"] in dry:
                critical_status.append({**c, "supplied": False})
            else:
                rows = dbm.run("sim_supply_path", Q.SIM_SUPPLY_PATH, {"target": c["pipe"]}, graph_name=sb)
                critical_status.append({**c, "supplied": bool(rows), "path": rows[0] if rows else None})
    out = customers_on(sorted(dry))
    intended = [c for c in out if c["pipe"] in segment_pipes]
    unintended = [c for c in out if c["pipe"] not in segment_pipes]
    return {
        "isolated": break_pipe in dry if break_pipe else None,
        "leak_path": leak_path,
        "dry_pipes": sorted(dry),
        "accounts_out": sum(c["accounts"] for c in out),
        "intended": intended,
        "unintended": unintended,
        "critical_out": [c for c in out if c["critical"]],
        "critical_status": critical_status,
    }


def plan_isolation(pipe_id: str, with_contingencies: bool = True) -> dict:
    """Find the valves that isolate a broken main, simulate closing them, and
    pre-compute what happens if any one of them will not turn."""
    info = pipe_info(pipe_id)
    if not info:
        return {"outcome": "not_in_graph", "reason": f"Pipe {pipe_id} is not in the graph."}
    seg = segment(pipe_id)
    boundary = boundary_valves(seg["members"])
    to_close = [v for v in boundary if v["status"] != "closed"]
    already = [v for v in boundary if v["status"] == "closed"]
    sim = simulate([v["id"] for v in to_close], pipe_id, seg["pipes"])
    warnings = []
    for v in to_close:
        if v["condition"] == "unknown":
            warnings.append(f"{v['id']} ({v['location']}) has unknown condition; last exercised {v['last_exercised']}.")
    for vid in seg["seized_valves"]:
        warnings.append(f"{vid} is recorded as seized, so the shutdown has to extend past it.")
    contingencies = []
    if with_contingencies:
        for v in to_close:
            contingencies.append(_contingency(pipe_id, seg, v, sim))
    return {
        "outcome": "planned" if sim["isolated"] else "cannot_isolate",
        "pipe": info,
        "segment": seg,
        "close": to_close,
        "already_closed": already,
        "simulation": sim,
        "warnings": warnings,
        "contingencies": contingencies,
        "history": dbm.run("pipe_history", Q.PIPE_HISTORY, {"pipe": pipe_id}),
    }


def _contingency(pipe_id: str, seg: dict, failed: dict, base_sim: dict) -> dict:
    """If `failed` will not close, the segment merges with whatever is on its
    other side. Re-derive the boundary for the merged area and simulate it."""
    nb = dbm.run("valve_neighbours_one", Q.VALVE_NEIGHBOURS_ONE, {"id": failed["id"]})
    neighbours = nb[0]["neighbours"] if nb else []
    beyond = [n for n in neighbours if n not in seg["members"]]
    merged = list(seg["members"]) + [failed["id"]]
    merged_pipes = list(seg["pipes"])
    for n in beyond:
        other = dbm.run("segment_of_node", Q.SEGMENT_OF_NODE, {"node": n})
        for r in other:
            if r["id"] not in merged:
                merged.append(r["id"])
                if r["kind"] == "Pipe":
                    merged_pipes.append(r["id"])
    boundary = [v for v in boundary_valves(merged) if v["id"] != failed["id"]]
    close = [v["id"] for v in boundary if v["status"] != "closed"]
    sim = simulate(close, pipe_id, merged_pipes)
    return {
        "if_fails": failed["id"],
        "close_instead": close,
        "accounts_out": sim["accounts_out"],
        "extra_accounts": sim["accounts_out"] - base_sim["accounts_out"],
        "critical_out": [c["name"] for c in sim["critical_out"]],
        "isolated": sim["isolated"],
    }


def check_plan(pipe_id: str, valve_ids: list[str]) -> dict:
    """Verify a plan someone else proposed (e.g. 'just shut the two nearest')."""
    seg = segment(pipe_id)
    sim = simulate(valve_ids, pipe_id, seg["pipes"])
    return {"pipe": pipe_id, "proposed": valve_ids, "simulation": sim,
            "verdict": "isolates the break" if sim["isolated"] else "does NOT isolate the break"}


# ---------------------------------------------------------------- actions

def report_valve(valve_id: str, condition: str, note: str = "") -> dict:
    v = valve_info(valve_id)
    if not v:
        return {"outcome": "not_in_graph", "reason": f"Valve {valve_id} is not in the graph."}
    dbm.run("report_valve", Q.REPORT_VALVE, {"id": valve_id, "condition": condition, "at": now(),
                                              "note": note[:200]}, write=True)
    dbm.run("mark_seized" if condition == "seized" else "clear_seized",
            Q.MARK_SEIZED if condition == "seized" else Q.CLEAR_SEIZED, {"id": valve_id}, write=True)
    return {"outcome": "recorded", "valve": valve_id, "location": v["location"], "condition": condition}


def issue_work_order(pipe_id: str, valve_ids: list[str], report: str = "") -> dict:
    """Commit a shutdown. Re-simulates first and refuses anything that does not
    isolate the break or relies on a valve recorded as seized."""
    if not pipe_info(pipe_id):
        return {"outcome": "refused", "reason": f"Pipe {pipe_id} is not in the graph."}
    bad = []
    for vid in valve_ids:
        v = valve_info(vid)
        if not v:
            bad.append(f"{vid} is not in the graph")
        elif v["condition"] == "seized":
            bad.append(f"{vid} is recorded as seized")
    if bad:
        return {"outcome": "refused", "reason": "; ".join(bad)}
    seg = segment(pipe_id)
    sim = simulate(valve_ids, pipe_id, seg["pipes"])
    if not sim["isolated"]:
        return {"outcome": "refused", "reason": "Simulation shows the break would still be fed.",
                "leak_path": sim["leak_path"]}
    n = dbm.run("count_work_orders", Q.COUNT_WORK_ORDERS)[0]["n"] + 1
    wo, inc, at = f"WO-{1000 + n}", f"INC-{datetime.now().year}-{900 + n}", now()
    dbm.run("create_work_order", Q.CREATE_WORK_ORDER, {
        "pipe": pipe_id, "incident": inc, "wo": wo, "at": at, "report": report[:300],
        "accounts_out": sim["accounts_out"], "critical_out": len(sim["critical_out"])}, write=True)
    dbm.run("work_order_close_valves", Q.WORK_ORDER_CLOSE_VALVES, {"wo": wo, "valves": valve_ids}, write=True)
    notices = [{"id": c["id"], "priority": "critical" if c["critical"] else "standard"}
               for c in sim["intended"] + sim["unintended"]]
    if notices:
        dbm.run("work_order_notify", Q.WORK_ORDER_NOTIFY, {"wo": wo, "notices": notices}, write=True)
    return {
        "outcome": "issued", "work_order": wo, "incident": inc, "pipe": pipe_id, "close_sequence": valve_ids,
        "accounts_out": sim["accounts_out"],
        "priority_notices": [c["name"] for c in sim["critical_out"]],
        "standard_notices": len([n for n in notices if n["priority"] == "standard"]),
        "dispatch": "simulated: no crew is paged and no message is sent",
    }


def restore_work_order(wo: str) -> dict:
    rows = dbm.run("restore_work_order", Q.RESTORE_WORK_ORDER, {"wo": wo, "at": now()}, write=True)
    if not rows:
        return {"outcome": "not_in_graph", "reason": f"Work order {wo} not found."}
    return {"outcome": "restored", "work_order": wo, "incident": rows[0]["incident"]}


def open_work_orders() -> list[dict]:
    return dbm.run("open_work_orders", Q.OPEN_WORK_ORDERS)


def supply_check(customer: str) -> dict:
    hits = dbm.run("customer_search", Q.CUSTOMER_SEARCH, {"q": customer.strip()[:60]})
    if not hits:
        return {"outcome": "not_in_graph", "reason": f"No customer matching '{customer}'."}
    c = hits[0]
    with sandbox() as sb:
        dbm.run("sim_apply_closures", Q.SIM_APPLY_CLOSURES, {"close": []}, write=True, graph_name=sb)
        rows = dbm.run("sim_supply_path", Q.SIM_SUPPLY_PATH, {"target": c["pipe"]}, graph_name=sb)
    return {"outcome": "supplied" if rows else "no_supply", "customer": c["name"], "pipe": c["pipe"],
            "path": rows[0] if rows else None}


def valve_priorities(limit: int = 8) -> dict:
    """Rank valves by how bad it would be if each one turned out to be seized
    during a shutdown next to it. One sandbox; labels toggled per scenario."""
    seg_rows = dbm.run("all_segments", Q.ALL_SEGMENTS)
    seg_of = {r["id"]: r["segment"] for r in seg_rows}
    members: dict[int, list[str]] = {}
    for r in seg_rows:
        members.setdefault(r["segment"], []).append(r["id"])
    valves = dbm.run("valve_neighbours", Q.VALVE_NEIGHBOURS)
    adj = {v["id"]: v["neighbours"] for v in valves}
    closed_now = {v["id"] for v in valves if v["status"] == "closed"}
    # valves bounding each segment
    bounds: dict[int, set[str]] = {}
    for v in valves:
        for n in v["neighbours"]:
            if n in seg_of:
                bounds.setdefault(seg_of[n], set()).add(v["id"])
    cust = dbm.run("snapshot_customers", Q.SNAPSHOT_CUSTOMERS)
    by_pipe: dict[str, list[dict]] = {}
    for c in cust:
        by_pipe.setdefault(c["pipe"], []).append(c)

    results = []
    t = dbm.current_trace()
    n_before = len(t.queries) if t else 0
    with sandbox() as sb:
        # Pipes already dry (e.g. an active work order) are not this valve's fault.
        baseline = {r["pipe"] for r in dbm.run("sim_dry_pipes_by_label", Q.SIM_DRY_PIPES_BY_LABEL, graph_name=sb)}
        for v in valves:
            if v["status"] == "closed" or v["condition"] == "seized":
                continue
            sides = {seg_of[n] for n in adj[v["id"]] if n in seg_of}
            if not sides:
                continue
            merged = set().union(*(bounds.get(s, set()) for s in sides)) - {v["id"]}
            ids = sorted(merged)
            dbm.run("sim_unset_open", Q.SIM_UNSET_OPEN, {"ids": ids}, write=True, graph_name=sb)
            dry = {r["pipe"] for r in dbm.run("sim_dry_pipes_by_label", Q.SIM_DRY_PIPES_BY_LABEL, graph_name=sb)}
            dry -= baseline
            # Only re-open what this loop closed; valves closed by a work order stay closed.
            dbm.run("sim_set_open", Q.SIM_SET_OPEN, {"ids": [i for i in ids if i not in closed_now]},
                    write=True, graph_name=sb)
            out = [c for p in dry for c in by_pipe.get(p, [])]
            accounts = sum(c["accounts"] for c in out)
            critical = sorted({c["name"] for c in out if c["critical"]})
            year = int(str(v["last_exercised"])[:4])
            age = max(datetime.now().year - year, 0)
            score = (accounts + 60 * len(critical)) * (1.5 if v["condition"] == "unknown" else 1.0) * (1 + age / 5)
            results.append({"valve": v["id"], "location": v["location"], "condition": v["condition"],
                            "last_exercised": v["last_exercised"], "accounts_out_if_seized": accounts,
                            "critical_out_if_seized": critical, "score": round(score, 1)})
    if t is not None:  # keep the evidence panel readable: summarise the loop
        loop = t.queries[n_before:]
        del t.queries[n_before:]
        t.queries.append(dbm.QueryRecord(
            "valve_priority_loop", f"{len(loop)} sandbox queries: SIM_UNSET_OPEN -> SIM_DRY_PIPES_BY_LABEL -> SIM_SET_OPEN per valve",
            {}, len(results), round(sum(q.ms for q in loop), 1), "sandbox"))
    results.sort(key=lambda r: -r["score"])
    return {"ranked": results[:limit], "valves_evaluated": len(results)}


def snapshot() -> dict:
    nodes = dbm.run("snapshot_nodes", Q.SNAPSHOT_NODES)
    edges = dbm.run("snapshot_edges", Q.SNAPSHOT_EDGES)
    customers = dbm.run("snapshot_customers", Q.SNAPSHOT_CUSTOMERS)
    for n in nodes:
        n["kind"] = n["labels"][0]
        n["seized"] = "Seized" in n["labels"]
    with sandbox() as sb:  # what is dry right now (active work orders)
        dbm.run("sim_apply_closures", Q.SIM_APPLY_CLOSURES, {"close": []}, write=True, graph_name=sb)
        dry = sorted(r["pipe"] for r in dbm.run("sim_dry_pipes", Q.SIM_DRY_PIPES, graph_name=sb))
    return {"nodes": nodes, "edges": edges, "customers": customers, "work_orders": open_work_orders(),
            "dry_pipes": dry}
