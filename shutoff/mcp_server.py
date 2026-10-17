"""MCP server exposing ShutOff's fixed graph tools to any MCP client
(Claude Desktop, Claude Code, ...).

    python -m shutoff.mcp_server                 # stdio
    python -m shutoff.mcp_server --http 8765     # streamable HTTP at /mcp

Same catalogue and validation as the web agent (tools.py). The two
state-changing actions are annotated as destructive and require
confirmed_by_human=true, so the client must put a person in the loop.
"""

from __future__ import annotations

import argparse
import json

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from . import tools as T

server = MCPServer(
    name="shutoff",
    title="ShutOff water-main isolation tools",
    instructions=("Tools over a synthetic water network stored in FalkorDB. Locate a break, plan its isolation, "
                  "verify proposed closures, record seized valves. Closing valves (issue_work_order) needs an "
                  "explicit human confirmation."),
    version="1.0",
)

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


def _out(name: str, args: dict) -> str:
    return json.dumps(T.call(name, args), default=str)


@server.tool(description=T.TOOLS["locate_break"]["description"], annotations=READ)
def locate_break(street: str = "", cross_a: str = "", cross_b: str = "", landmark: str = "") -> str:
    return _out("locate_break", {"street": street, "cross_a": cross_a, "cross_b": cross_b, "landmark": landmark})


@server.tool(description=T.TOOLS["plan_isolation"]["description"], annotations=READ)
def plan_isolation(pipe_id: str) -> str:
    return _out("plan_isolation", {"pipe_id": pipe_id})


@server.tool(description=T.TOOLS["check_closure_plan"]["description"], annotations=READ)
def check_closure_plan(pipe_id: str, valve_ids: list[str]) -> str:
    return _out("check_closure_plan", {"pipe_id": pipe_id, "valve_ids": valve_ids})


@server.tool(description=T.TOOLS["report_valve_condition"]["description"], annotations=WRITE)
def report_valve_condition(valve_id: str, condition: str, note: str = "") -> str:
    return _out("report_valve_condition", {"valve_id": valve_id, "condition": condition, "note": note})


@server.tool(description=T.TOOLS["supply_check"]["description"], annotations=READ)
def supply_check(customer: str) -> str:
    return _out("supply_check", {"customer": customer})


@server.tool(description=T.TOOLS["valve_exercise_priorities"]["description"], annotations=READ)
def valve_exercise_priorities(limit: int = 8) -> str:
    return _out("valve_exercise_priorities", {"limit": limit})


@server.tool(description=T.TOOLS["pipe_history"]["description"], annotations=READ)
def pipe_history(pipe_id: str) -> str:
    return _out("pipe_history", {"pipe_id": pipe_id})


@server.tool(description=T.TOOLS["issue_work_order"]["description"], annotations=DESTRUCTIVE)
def issue_work_order(pipe_id: str, valve_ids: list[str], confirmed_by_human: bool, report: str = "",
                     acknowledge_critical: list[str] | None = None, expected_accounts_out: int | None = None,
                     expected_critical_ids: list[str] | None = None) -> str:
    return _out("issue_work_order", {"pipe_id": pipe_id, "valve_ids": valve_ids, "report": report,
                                     "confirmed_by_human": confirmed_by_human,
                                     "acknowledge_critical": acknowledge_critical or [],
                                     "expected_accounts_out": expected_accounts_out,
                                     "expected_critical_ids": expected_critical_ids or []})


@server.tool(description=T.TOOLS["restore_service"]["description"], annotations=DESTRUCTIVE)
def restore_service(work_order_id: str) -> str:
    return _out("restore_service", {"work_order_id": work_order_id})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, default=None, help="serve streamable HTTP on this port instead of stdio")
    a = ap.parse_args()
    if a.http:
        server.run(transport="streamable-http", port=a.http)
    else:
        server.run()
