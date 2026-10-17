"""Isolation planning, simulation and actions over the FalkorDB graph.

Read paths use the live graph (read-only queries). "What would happen if we
closed these valves" runs on a sandbox: a GRAPH.COPY of the live network that
is thrown away afterwards. Only issue_work_order / report_valve /
restore_work_order write to the live graph, and issue_work_order re-runs the
simulation itself instead of trusting whoever called it.
"""

from __future__ import annotations

import re
import time
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


def _copy_with_retry(name: str, attempts: int = 60, wait_s: float = 0.05) -> None:
    """GRAPH.COPY forks the server. Redis allows one child process at a time, so a
    copy that lands while an automatic background save (BGSAVE) is running fails
    with "could not fork". That window lasts milliseconds: wait and retry."""
    for i in range(attempts):
        try:
            dbm.graph().copy(name)
            return
        except Exception as e:
            if "fork" not in str(e).lower() or i == attempts - 1:
                raise
            time.sleep(wait_s)


@contextmanager
def sandbox():
    """A throwaway GRAPH.COPY of the live network."""
    name = f"{dbm.GRAPH}__sim_{uuid.uuid4().hex[:8]}"
    t = dbm.current_trace()
    if t is not None:
        t.queries.append(dbm.QueryRecord("sandbox_copy", f"GRAPH.COPY {dbm.GRAPH} {name}", {}, 0, 0, dbm.GRAPH))
    _copy_with_retry(name)
    try:
        yield name
    finally:
        try:
            dbm.graph(name).delete()
        except Exception:
            pass


def simulate(close: list[str], break_pipe: str | None = None, segment_pipes: list[str] | None = None) -> dict:
    """Close `close` on a sandbox copy and report what happens: is the break
    still fed, who loses water *because of these closures*, are critical
    customers still supplied (with the path that proves it).

    Valves already closed (other work orders) are applied first and measured as
    a baseline, so outages caused by other work orders are reported separately
    instead of being counted against this plan."""
    segment_pipes = segment_pipes or []
    with sandbox() as sb:
        dbm.run("sim_delete_closed", Q.SIM_DELETE_CLOSED, write=True, graph_name=sb)
        baseline = {r["pipe"] for r in dbm.run("sim_dry_pipes", Q.SIM_DRY_PIPES, graph_name=sb)}
        dbm.run("sim_delete_valves", Q.SIM_DELETE_VALVES, {"close": close}, write=True, graph_name=sb)
        dry = {r["pipe"] for r in dbm.run("sim_dry_pipes", Q.SIM_DRY_PIPES, graph_name=sb)}
        leak_path = None
        if break_pipe and break_pipe not in dry:
            rows = dbm.run("sim_supply_path", Q.SIM_SUPPLY_PATH, {"target": break_pipe}, graph_name=sb)
            leak_path = rows[0] if rows else None
        critical = dbm.run("critical_customers", Q.CRITICAL_CUSTOMERS, graph_name=sb)
        critical_status = []
        for c in critical:
            if c["pipe"] in dry:
                critical_status.append({**c, "supplied": False, "already_out": c["pipe"] in baseline})
            else:
                rows = dbm.run("sim_supply_path", Q.SIM_SUPPLY_PATH, {"target": c["pipe"]}, graph_name=sb)
                critical_status.append({**c, "supplied": bool(rows), "path": rows[0] if rows else None})
    new_dry = dry - baseline
    out = customers_on(sorted(new_dry))
    already = customers_on(sorted(baseline))
    intended = [c for c in out if c["pipe"] in segment_pipes]
    unintended = [c for c in out if c["pipe"] not in segment_pipes]
    return {
        "isolated": break_pipe in dry if break_pipe else None,
        "leak_path": leak_path,
        "dry_pipes": sorted(new_dry),
        "accounts_out": sum(c["accounts"] for c in out),
        "intended": intended,
        "unintended": unintended,
        "critical_out": [c for c in out if c["critical"]],
        "critical_status": critical_status,
        "already_out_accounts": sum(c["accounts"] for c in already),
        "already_dry_pipes": sorted(baseline),
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


def issue_work_order(pipe_id: str, valve_ids: list[str], report: str = "",
                     expected: dict | None = None, acknowledge_critical: list[str] | None = None) -> dict:
    """Commit a shutdown. Re-simulates first and refuses anything that does not
    isolate the break, relies on a seized valve, has a different impact from
    the one the approver was shown (`expected`), or cuts off a critical
    customer the approver did not explicitly acknowledge."""
    valve_ids = list(dict.fromkeys(valve_ids))  # de-duplicate, keep order
    if not pipe_info(pipe_id):
        return {"outcome": "refused", "reason": f"Pipe {pipe_id} is not in the graph."}
    if not valve_ids:
        return {"outcome": "refused", "reason": "No valves to close."}
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
    critical = sorted({c["id"]: c["name"] for c in sim["critical_out"]}.items())
    critical_ids = [cid for cid, _ in critical]
    if expected is not None:
        was = (expected.get("accounts_out"), sorted(expected.get("critical_ids") or []))
        now_ = (sim["accounts_out"], critical_ids)
        if was != now_:
            return {"outcome": "refused", "stale": True,
                    "reason": (f"The impact changed since the plan was shown (was {was[0]} accounts, critical "
                               f"{was[1] or 'none'}; now {now_[0]} accounts, critical {now_[1] or 'none'}). "
                               "Re-plan before approving.")}
    if critical_ids and sorted(set(acknowledge_critical or [])) != critical_ids:
        return {"outcome": "needs_acknowledgement",
                "critical": [{"id": cid, "name": name} for cid, name in critical],
                "reason": ("This plan cuts off critical customer(s): " + ", ".join(n for _, n in critical) +
                           ". The approver must acknowledge each one before valves are closed.")}
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
        "acknowledged_critical": critical_ids,
        "standard_notices": len([n for n in notices if n["priority"] == "standard"]),
        "dispatch": "simulated: no crew is paged and no message is sent",
    }


def restore_work_order(wo: str) -> dict:
    """Re-open a work order's valves, but only those no other active work order
    still needs. Each valve is re-opened on a sandbox first; if that would re-feed
    another active break, it stays closed and the dependent work order takes it over."""
    rows = dbm.run("work_order_by_id", Q.WORK_ORDER_BY_ID, {"wo": wo})
    if not rows:
        return {"outcome": "not_in_graph", "reason": f"Work order {wo} not found."}
    if rows[0]["status"] != "issued":
        return {"outcome": "refused", "reason": f"{wo} is already {rows[0]['status']}."}
    others = [w for w in open_work_orders() if w["id"] != wo]
    reopen: list[str] = []
    kept: list[dict] = []
    with sandbox() as sb:
        for v in sorted(set(rows[0]["valves"])):
            holders = [w["id"] for w in others if v in w["valves"]]
            if holders:
                kept.append({"valve": v, "held_by": holders, "reason": "also closed by another work order"})
                continue
            dbm.run("sim_set_open", Q.SIM_SET_OPEN, {"ids": [v]}, write=True, graph_name=sb)
            dry = {r["pipe"] for r in dbm.run("sim_dry_pipes_by_label", Q.SIM_DRY_PIPES_BY_LABEL, graph_name=sb)}
            refed = [w["id"] for w in others if w["pipe"] not in dry]
            if refed:
                dbm.run("sim_unset_open", Q.SIM_UNSET_OPEN, {"ids": [v]}, write=True, graph_name=sb)
                kept.append({"valve": v, "held_by": refed, "reason": "re-opening it would re-feed another active break"})
            else:
                reopen.append(v)
    if reopen:
        dbm.run("reopen_valves", Q.REOPEN_VALVES, {"ids": reopen}, write=True)
    for k in kept:
        if k["reason"].startswith("re-opening"):
            for holder in k["held_by"]:
                dbm.run("transfer_hold", Q.TRANSFER_HOLD, {"to": holder, "valve": k["valve"], "from": wo}, write=True)
    done = dbm.run("close_out_work_order", Q.CLOSE_OUT_WORK_ORDER,
                   {"wo": wo, "at": now(), "kept": [k["valve"] for k in kept]}, write=True)
    return {"outcome": "restored", "work_order": wo, "incident": done[0]["incident"] if done else rows[0]["incident"],
            "reopened": reopen, "kept_closed": kept}


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
