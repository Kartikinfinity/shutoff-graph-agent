"""Integration tests against a real FalkorDB (docker compose up -d)."""

from conftest import needs_db

from shutoff import agent, town
from shutoff import db as dbm
from shutoff import network as N
from shutoff import tools as T

pytestmark = needs_db

PLAN_A = ["V-212", "V-213", "V-264", "V-265"]
PLAN_B = ["V-212", "V-213", "V-222", "V-223", "V-264", "V-266", "V-292"]


# ------------------------------------------------------------ independent check of the graph algorithm

def _python_segments():
    """Union-find over the raw town data, independent of FalkorDB:
    connect junctions and pipes, treat every valve as a wall."""
    net = town.build()
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keep = {n["id"] for n in net["junctions"] + net["pipes"]}
    for k in keep:
        find(k)
    for a, b in net["links"]:
        if a in keep and b in keep:
            parent[find(a)] = find(b)
    groups = {}
    for k in keep:
        groups.setdefault(find(k), set()).add(k)
    return {k: frozenset(g) for g in groups.values() for k in g}


def test_falkordb_wcc_segments_match_independent_union_find(fresh):
    expected = _python_segments()
    for p in town.build()["pipes"]:
        got = set(N.segment(p["id"])["members"])
        assert got == expected[p["id"]], p["id"]


# ------------------------------------------------------------ the demo scenario

def test_plan_a_isolates_the_maple_break_with_four_valves(fresh):
    r = N.plan_isolation("P-107")
    assert r["outcome"] == "planned"
    assert [v["id"] for v in r["close"]] == PLAN_A
    sim = r["simulation"]
    assert sim["isolated"] is True
    assert sim["accounts_out"] == 34
    assert sim["unintended"] == [] and sim["critical_out"] == []
    assert set(r["segment"]["pipes"]) == {"P-107", "P-136"}


def test_plan_a_warns_about_v265_and_precomputes_the_dialysis_risk(fresh):
    r = N.plan_isolation("P-107")
    assert any("V-265" in w and "2011-04" in w for w in r["warnings"])
    c = next(c for c in r["contingencies"] if c["if_fails"] == "V-265")
    assert sorted(c["close_instead"]) == sorted(PLAN_B)
    assert c["accounts_out"] == 66
    assert c["critical_out"] == ["Willow Ct medical-priority account (home dialysis)"]


def test_naive_two_valve_plan_is_rejected_with_the_leak_path(fresh):
    r = N.check_plan("P-107", ["V-212", "V-213"])
    sim = r["simulation"]
    assert sim["isolated"] is False
    path = sim["leak_path"]["path"]
    assert path[0].startswith("S-") and path[-1] == "P-107"
    assert "V-212" not in path and "V-213" not in path
    links = {frozenset(l) for l in town.build()["links"]}
    for a, b in zip(path, path[1:]):  # every hop is a real pipe connection
        assert frozenset((a, b)) in links


def test_seized_valve_forces_plan_b_and_flags_unintended_cutoff(fresh):
    assert N.report_valve("V-265", "seized", "crew report")["outcome"] == "recorded"
    r = N.plan_isolation("P-107")
    assert sorted(v["id"] for v in r["close"]) == sorted(PLAN_B)
    sim = r["simulation"]
    assert sim["isolated"] and sim["accounts_out"] == 66
    assert {c["id"] for c in sim["unintended"]} == {"C-MED7", "C-151"}
    assert [c["id"] for c in sim["critical_out"]] == ["C-MED7"]
    # marking it ok again restores plan A
    N.report_valve("V-265", "ok")
    assert [v["id"] for v in N.plan_isolation("P-107")["close"]] == PLAN_A


def test_hospital_stays_supplied_in_plan_b_with_a_verifiable_path(fresh):
    N.report_valve("V-265", "seized")
    r = N.plan_isolation("P-107")
    hosp = next(c for c in r["simulation"]["critical_status"] if c["id"] == "C-HOSP")
    assert hosp["supplied"] is True
    assert hosp["path"]["path"][0].startswith("S-") and hosp["path"]["path"][-1] == "P-119"


# ------------------------------------------------------------ actions and safety

def test_work_order_refuses_seized_valves_and_leaky_plans(fresh):
    N.report_valve("V-265", "seized")
    assert N.issue_work_order("P-107", PLAN_A)["outcome"] == "refused"
    r = N.issue_work_order("P-107", ["V-212", "V-213"])
    assert r["outcome"] == "refused"  # V-265 seized is in neither, but the plan leaks
    assert N.issue_work_order("P-999", ["V-212"])["outcome"] == "refused"
    assert N.issue_work_order("P-107", ["V-999"])["outcome"] == "refused"
    assert N.open_work_orders() == []


def test_issuing_a_work_order_changes_the_live_graph_and_restore_reverts_it(fresh):
    r = N.issue_work_order("P-107", PLAN_A, "test")
    assert r["outcome"] == "issued"
    rows = dbm.run("t", "MATCH (v:Valve) WHERE v.id IN $ids RETURN v.status AS s, 'Open' IN labels(v) AS open",
                   {"ids": PLAN_A})
    assert all(x["s"] == "closed" and not x["open"] for x in rows) and len(rows) == 4
    wo = r["work_order"]
    n = dbm.run("t", "MATCH (:WorkOrder {id: $wo})-[c:CLOSES]->(:Valve) RETURN count(c) AS n", {"wo": wo})[0]["n"]
    assert n == 4
    # the live graph now has a dry area, and a plan elsewhere sees the closures
    snap = N.snapshot()
    assert {"P-107", "P-136"} <= set(snap["dry_pipes"])
    assert N.restore_work_order(wo)["outcome"] == "restored"
    rows = dbm.run("t", "MATCH (v:Valve) WHERE v.id IN $ids RETURN v.status AS s", {"ids": PLAN_A})
    assert all(x["s"] == "open" for x in rows)
    assert N.snapshot()["dry_pipes"] == []


def test_critical_customer_gets_a_priority_notice(fresh):
    N.report_valve("V-265", "seized")
    r = N.issue_work_order("P-107", PLAN_B)
    assert r["priority_notices"] == ["Willow Ct medical-priority account (home dialysis)"]
    rows = dbm.run("t", """MATCH (:WorkOrder {id: $wo})-[n:NOTIFIES]->(c:Customer {id: 'C-MED7'})
                           RETURN n.priority AS p, n.channel AS ch""", {"wo": r["work_order"]})
    assert rows == [{"p": "critical", "ch": "simulated"}]


def test_simulation_never_modifies_the_live_graph(fresh):
    before = dbm.run("t", "MATCH (v:Valve:Open) RETURN count(v) AS n")[0]["n"]
    N.simulate(PLAN_B, "P-107")
    N.valve_priorities(5)
    after = dbm.run("t", "MATCH (v:Valve:Open) RETURN count(v) AS n")[0]["n"]
    assert before == after == 96
    assert not [g for g in dbm.db().list_graphs() if "__sim_" in g]


def test_read_queries_cannot_write(fresh):
    import pytest
    with pytest.raises(Exception):
        dbm.run("t", "MATCH (v:Valve {id: 'V-212'}) SET v.status = 'closed'")


# ------------------------------------------------------------ missing evidence / failure cases

def test_unknown_and_ambiguous_locations_are_not_guessed(fresh):
    assert N.locate_break("Elm St", "1st Ave", "2nd Ave")["outcome"] == "not_in_graph"
    assert N.locate_break("Maple St", "9th Ave", "2nd Ave")["outcome"] == "not_in_graph"
    r = N.locate_break("Oak St", "3rd Ave")
    assert r["outcome"] == "ambiguous" and {c["pipe"] for c in r["candidates"]} == {"P-112", "P-113"}
    assert N.locate_break("", landmark="spaceport")["outcome"] == "not_in_graph"
    assert N.plan_isolation("P-999")["outcome"] == "not_in_graph"
    assert T.call("check_closure_plan", {"pipe_id": "P-107", "valve_ids": ["V-999"]})["outcome"] == "not_in_graph"
    assert N.report_valve("V-999", "seized")["outcome"] == "not_in_graph"
    assert N.supply_check("spaceport")["outcome"] == "not_in_graph"


def test_location_normalisation(fresh):
    assert N.locate_break("maple street", "second avenue", "3rd")["pipe"] == "P-107"
    assert N.locate_break("", landmark="hospital")["pipe"] == "P-119"


def test_valve_priorities_put_the_dialysis_risk_first_on_a_fresh_network(fresh):
    r = N.valve_priorities(5)
    top = r["ranked"][0]
    assert top["valve"] == "V-265"
    assert "Willow Ct medical-priority account (home dialysis)" in top["critical_out_if_seized"]


# ------------------------------------------------------------ agent (offline mode)

def test_offline_agent_conversation_end_to_end(fresh):
    sid = "test-convo"
    agent.SESSIONS.pop(sid, None)
    r = agent.handle(sid, "Water gushing on Maple St between 2nd and 3rd Ave")
    assert r["mode"] == "offline" and r["proposal"]["valves"] == PLAN_A
    r = agent.handle(sid, "Crew wants to just close V-212 and V-213")
    assert "does NOT isolate" in r["reply"]
    assert N.open_work_orders() == []  # proposing or checking never closes anything
    r = agent.handle(sid, "V-265 won't turn")
    assert sorted(r["proposal"]["valves"]) == sorted(PLAN_B)
    assert "CRITICAL" in r["reply"]
    r = agent.approve(sid)
    assert r["steps"][0]["result"]["outcome"] == "issued"
    assert len(N.open_work_orders()) == 1


def test_every_agent_step_carries_its_cypher_evidence(fresh):
    r = agent.handle("test-evidence", "Water gushing on Maple St between 2nd and 3rd Ave")
    plan = next(s for s in r["steps"] if s["tool"] == "plan_isolation")
    names = [q["name"] for q in plan["queries"]]
    assert "segment_of_pipe" in names and "sim_dry_pipes" in names and "sandbox_copy" in names
    assert all(q["cypher"] for q in plan["queries"])


# ------------------------------------------------------------ MCP server

def test_mcp_server_exposes_the_same_tools_and_gates_destructive_ones(fresh):
    import asyncio
    import json

    from shutoff.mcp_server import server

    async def go():
        tools = {t.name: t for t in await server.list_tools()}
        assert set(tools) == set(T.TOOLS)
        assert tools["issue_work_order"].annotations.destructive_hint is True
        assert tools["plan_isolation"].annotations.read_only_hint is True
        r = await server.call_tool("plan_isolation", {"pipe_id": "P-107"})
        assert json.loads(r.content[0].text)["outcome"] == "planned"
        r = await server.call_tool("issue_work_order", {"pipe_id": "P-107", "valve_ids": PLAN_A,
                                                        "confirmed_by_human": False})
        assert json.loads(r.content[0].text)["outcome"] == "refused"

    asyncio.run(go())
    assert N.open_work_orders() == []


# ------------------------------------------------------------ LLM loop (mocked client, no network)

class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content, self.stop_reason = content, stop_reason


def _fake_anthropic(script):
    """A stand-in for anthropic.Anthropic whose replies follow `script`."""
    calls = []

    class Messages:
        def create(self, **kw):
            calls.append(kw)
            return script[len(calls) - 1]

    class Beta:
        messages = Messages()

    class Client:
        beta = Beta()

    return Client, calls


def _run_llm(monkeypatch, script):
    import anthropic

    client_cls, calls = _fake_anthropic(script)
    monkeypatch.setattr(anthropic, "Anthropic", client_cls)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("SHUTOFF_OFFLINE", "0")
    agent.SESSIONS.pop("llm", None)
    return agent.handle("llm", "Water gushing on Maple St between 2nd and 3rd"), calls


def test_llm_loop_runs_tools_and_accepts_a_grounded_reply(fresh, monkeypatch):
    script = [
        _Resp([_Block(type="tool_use", id="t1", name="locate_break",
                      input={"street": "Maple St", "cross_a": "2nd Ave", "cross_b": "3rd Ave"})], "tool_use"),
        _Resp([_Block(type="tool_use", id="t2", name="plan_isolation", input={"pipe_id": "P-107"})], "tool_use"),
        _Resp([_Block(type="text", text="Close V-212, V-213, V-264 and V-265: 34 accounts lose water.")], "end_turn"),
    ]
    r, calls = _run_llm(monkeypatch, script)
    assert r["mode"] == "llm" and r["verified"] is True
    assert [s["tool"] for s in r["steps"]] == ["locate_break", "plan_isolation"]
    assert r["proposal"]["valves"] == PLAN_A
    assert {t["name"] for t in calls[0]["tools"]} == {t["name"] for t in T.model_tools()}
    assert N.open_work_orders() == []


def test_llm_reply_with_invented_valve_is_rejected_and_rebuilt(fresh, monkeypatch):
    script = [
        _Resp([_Block(type="tool_use", id="t1", name="plan_isolation", input={"pipe_id": "P-107"})], "tool_use"),
        _Resp([_Block(type="text", text="Close V-212 and V-777: 12 accounts lose water.")], "end_turn"),
    ]
    r, _ = _run_llm(monkeypatch, script)
    assert r["verified"] is False and "V-777" in r["note"]
    assert "V-777" not in r["reply"] and "34 accounts" in r["reply"]
