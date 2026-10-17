"""The fixed tool catalogue shared by the LLM agent and the MCP server.

Each tool validates its arguments, runs fixed queries through network.py and
returns plain JSON. The model chooses tools and fills typed arguments; it
never writes Cypher, and identifiers it supplies are checked against the
graph before anything runs.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from . import network as N

VALVE_RE = re.compile(r"^V-\d{3}$")
PIPE_RE = re.compile(r"^P-\d{3}$")
WO_RE = re.compile(r"^WO-\d{4}$")


class ToolError(ValueError):
    pass


def _valve(v: Any) -> str:
    v = str(v).strip().upper()
    if not VALVE_RE.match(v):
        raise ToolError(f"'{v}' is not a valve id (expected like V-265).")
    return v


def _pipe(p: Any) -> str:
    p = str(p).strip().upper()
    if not PIPE_RE.match(p):
        raise ToolError(f"'{p}' is not a pipe id (expected like P-107).")
    return p


def _text(s: Any, n: int = 80) -> str:
    return str(s or "").strip()[:n]


# ---------------------------------------------------------------- handlers

def locate_break(args: dict) -> dict:
    return N.locate_break(_text(args.get("street")), _text(args.get("cross_a")) or None,
                          _text(args.get("cross_b")) or None, _text(args.get("landmark")) or None)


def plan_isolation(args: dict) -> dict:
    return N.plan_isolation(_pipe(args["pipe_id"]))


def check_closure_plan(args: dict) -> dict:
    valves = [_valve(v) for v in (args.get("valve_ids") or [])][:12]
    if not valves:
        raise ToolError("valve_ids must list at least one valve.")
    unknown = [v for v in valves if not N.valve_info(v)]
    if unknown:
        return {"outcome": "not_in_graph", "reason": f"Unknown valve(s): {', '.join(unknown)}"}
    return N.check_plan(_pipe(args["pipe_id"]), valves)


def report_valve_condition(args: dict) -> dict:
    cond = _text(args.get("condition"), 10).lower()
    if cond not in ("seized", "ok"):
        raise ToolError("condition must be 'seized' or 'ok'.")
    return N.report_valve(_valve(args["valve_id"]), cond, _text(args.get("note"), 200))


def supply_check(args: dict) -> dict:
    return N.supply_check(_text(args.get("customer"), 60))


def valve_exercise_priorities(args: dict) -> dict:
    limit = max(1, min(int(args.get("limit", 8) or 8), 15))
    return N.valve_priorities(limit)


def pipe_history(args: dict) -> dict:
    pid = _pipe(args["pipe_id"])
    info = N.pipe_info(pid)
    if not info:
        return {"outcome": "not_in_graph", "reason": f"Pipe {pid} is not in the graph."}
    from . import db as dbm, queries as Q
    return {"pipe": info, "incidents": dbm.run("pipe_history", Q.PIPE_HISTORY, {"pipe": pid})}


def issue_work_order(args: dict) -> dict:
    """Only exposed to callers that have a human approval step (UI button, or
    an MCP client that asks the user before each tool call)."""
    if args.get("confirmed_by_human") is not True:
        return {"outcome": "refused", "reason": "A human must confirm before valves are closed."}
    valves = [_valve(v) for v in (args.get("valve_ids") or [])][:12]
    return N.issue_work_order(_pipe(args["pipe_id"]), valves, _text(args.get("report"), 300))


def restore_service(args: dict) -> dict:
    wo = _text(args.get("work_order_id"), 10).upper()
    if not WO_RE.match(wo):
        raise ToolError(f"'{wo}' is not a work order id (expected like WO-1001).")
    return N.restore_work_order(wo)


# ---------------------------------------------------------------- catalogue

def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOLS: dict[str, dict] = {
    "locate_break": {
        "fn": locate_break,
        "description": ("Resolve a reported break location to one water main (pipe) in the network graph. "
                        "Give the street the break is on and the two cross streets it is between, or a "
                        "landmark/customer name. Returns found / ambiguous (with candidates) / not_in_graph."),
        "schema": _schema({
            "street": {"type": "string", "description": "Street the broken main runs along, e.g. 'Maple St'."},
            "cross_a": {"type": "string", "description": "First cross street, e.g. '2nd Ave'."},
            "cross_b": {"type": "string", "description": "Second cross street, e.g. '3rd Ave'."},
            "landmark": {"type": "string", "description": "A customer or landmark near the break, e.g. 'hospital'."},
        }, []),
    },
    "plan_isolation": {
        "fn": plan_isolation,
        "description": ("Find the valves that isolate a broken main (graph connected components), simulate "
                        "closing them on a sandbox copy of the network, list who loses water (including "
                        "unintended cut-offs and critical customers) and pre-compute a backup plan for each "
                        "valve in case it will not turn. Also returns the pipe's break history."),
        "schema": _schema({"pipe_id": {"type": "string", "description": "Pipe id like P-107."}}, ["pipe_id"]),
    },
    "check_closure_plan": {
        "fn": check_closure_plan,
        "description": ("Verify a closure plan someone proposed (e.g. 'just shut the two nearest valves'). "
                        "Simulates it and, if the break is still fed, returns the exact path water takes."),
        "schema": _schema({
            "pipe_id": {"type": "string"},
            "valve_ids": {"type": "array", "items": {"type": "string"}, "description": "Valve ids like V-212."},
        }, ["pipe_id", "valve_ids"]),
    },
    "report_valve_condition": {
        "fn": report_valve_condition,
        "description": ("Record a field crew report about a valve in the graph: 'seized' (cannot be turned) "
                        "or 'ok'. Seized valves are treated as pass-through by every later isolation plan."),
        "schema": _schema({
            "valve_id": {"type": "string"},
            "condition": {"type": "string", "enum": ["seized", "ok"]},
            "note": {"type": "string"},
        }, ["valve_id", "condition"]),
    },
    "supply_check": {
        "fn": supply_check,
        "description": "Check whether a customer (by name) is supplied right now, and return the supply path from a source.",
        "schema": _schema({"customer": {"type": "string"}}, ["customer"]),
    },
    "valve_exercise_priorities": {
        "fn": valve_exercise_priorities,
        "description": ("Rank valves for preventive exercising by how many accounts (and which critical "
                        "customers) would lose water if that valve were found seized during a shutdown."),
        "schema": _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 15}}, []),
    },
    "pipe_history": {
        "fn": pipe_history,
        "description": "Past incidents recorded on a pipe, with its material and install year.",
        "schema": _schema({"pipe_id": {"type": "string"}}, ["pipe_id"]),
    },
    # Human-gated actions: not offered to the chat model.
    "issue_work_order": {
        "fn": issue_work_order,
        "human_gated": True,
        "description": ("Commit a shutdown: closes the listed valves in the graph, opens an Incident and a "
                        "WorkOrder, and records notices (dispatch and notices are simulated). Re-simulates "
                        "first and refuses unless the break is isolated. Requires confirmed_by_human=true."),
        "schema": _schema({
            "pipe_id": {"type": "string"},
            "valve_ids": {"type": "array", "items": {"type": "string"}},
            "report": {"type": "string"},
            "confirmed_by_human": {"type": "boolean"},
        }, ["pipe_id", "valve_ids", "confirmed_by_human"]),
    },
    "restore_service": {
        "fn": restore_service,
        "human_gated": True,
        "description": "Re-open the valves of a work order and mark its incident resolved.",
        "schema": _schema({"work_order_id": {"type": "string"}}, ["work_order_id"]),
    },
}


def call(name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"outcome": "error", "reason": f"Unknown tool {name}."}
    try:
        return TOOLS[name]["fn"](args or {})
    except ToolError as e:
        return {"outcome": "invalid_arguments", "reason": str(e)}
    except KeyError as e:
        return {"outcome": "invalid_arguments", "reason": f"Missing argument {e}."}


def model_tools() -> list[dict]:
    """Tool definitions for the Claude API (human-gated tools excluded).

    Arguments are validated server-side by each handler, so strict schema
    mode is not relied on."""
    return [{"name": n, "description": t["description"], "input_schema": t["schema"]}
            for n, t in TOOLS.items() if not t.get("human_gated")]
