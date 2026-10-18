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
    r = N.issue_work_order("P-107", PLAN_B, acknowledge_critical=["C-MED7"])
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
    assert "still connects the break" in r["reply"]
    assert N.open_work_orders() == []  # proposing or checking never closes anything
    r = agent.handle(sid, "V-265 won't turn")
    assert sorted(r["proposal"]["valves"]) == sorted(PLAN_B)
    assert "CRITICAL" in r["reply"]
    r = agent.approve(sid)  # critical cut-off, not acknowledged
    assert r["steps"][0]["result"]["outcome"] == "needs_acknowledgement"
    assert N.open_work_orders() == []
    r = agent.approve(sid, ["C-MED7"])
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
                                                        "confirmed_by_human": False, "expected_accounts_out": 34})
        assert json.loads(r.content[0].text)["outcome"] == "refused"
        try:  # the shown impact is a required argument of the MCP tool
            await server.call_tool("issue_work_order", {"pipe_id": "P-107", "valve_ids": PLAN_A,
                                                        "confirmed_by_human": True})
            raise AssertionError("issue_work_order accepted a call without expected_accounts_out")
        except AssertionError:
            raise
        except Exception:
            pass
        r = await server.call_tool("issue_work_order", {"pipe_id": "P-107", "valve_ids": PLAN_A,
                                                        "confirmed_by_human": True, "expected_accounts_out": 34,
                                                        "expected_critical_ids": []})
        wo = json.loads(r.content[0].text)["work_order"]
        r = await server.call_tool("restore_service", {"work_order_id": wo, "confirmed_by_human": False})
        assert json.loads(r.content[0].text)["outcome"] == "refused"
        r = await server.call_tool("restore_service", {"work_order_id": wo, "confirmed_by_human": True})
        assert json.loads(r.content[0].text)["outcome"] == "restored"

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



# ------------------------------------------------------------ audit regressions (17 Oct 2026)

def _dry_now():
    return set(N.snapshot()["dry_pipes"])


def test_restore_never_reopens_a_valve_another_active_work_order_holds(fresh):
    wo1 = N.issue_work_order("P-107", PLAN_A)["work_order"]
    wo2 = N.issue_work_order("P-108", ["V-214", "V-213"])["work_order"]  # V-213 also held by wo1
    r = N.restore_work_order(wo2)
    assert r["outcome"] == "restored"
    assert [k["valve"] for k in r["kept_closed"]] == ["V-213"]
    assert N.valve_info("V-213")["status"] == "closed"
    assert "P-107" in _dry_now()  # the first break is still isolated


def test_restore_keeps_valves_another_break_relies_on_and_hands_them_over(fresh):
    wo1 = N.issue_work_order("P-107", PLAN_A)["work_order"]
    plan = N.plan_isolation("P-108", with_contingencies=False)
    assert [v["id"] for v in plan["close"]] == ["V-214"]          # relies on V-213 closed by wo1
    assert [v["id"] for v in plan["already_closed"]] == ["V-213"]
    wo2 = N.issue_work_order("P-108", ["V-214"])["work_order"]
    r = N.restore_work_order(wo1)
    kept = {k["valve"]: k for k in r["kept_closed"]}
    assert set(kept) == {"V-213"} and kept["V-213"]["held_by"] == [wo2]
    assert set(r["reopened"]) == {"V-212", "V-264", "V-265"}
    dry = _dry_now()
    assert "P-108" in dry and "P-107" not in dry
    held = next(w for w in N.open_work_orders() if w["id"] == wo2)["valves"]
    assert "V-213" in held
    r = N.restore_work_order(wo2)
    assert set(r["reopened"]) == {"V-213", "V-214"} and r["kept_closed"] == []
    assert _dry_now() == set()


def test_restore_is_not_repeatable(fresh):
    wo = N.issue_work_order("P-107", PLAN_A)["work_order"]
    assert N.restore_work_order(wo)["outcome"] == "restored"
    r = N.restore_work_order(wo)
    assert r["outcome"] == "refused" and "already restored" in r["reason"]
    assert N.restore_work_order("WO-9999")["outcome"] == "not_in_graph"


def test_plan_impact_excludes_outages_from_other_work_orders(fresh):
    alone = N.plan_isolation("P-119", with_contingencies=False)["simulation"]
    N.issue_work_order("P-107", PLAN_A)
    sim = N.plan_isolation("P-119", with_contingencies=False)["simulation"]
    assert sim["accounts_out"] == alone["accounts_out"]
    assert sim["already_out_accounts"] == 34
    assert not {"C-107", "C-136", "C-BAKERY"} & {c["id"] for c in sim["unintended"]}


def test_critical_cutoff_needs_exact_acknowledgement(fresh):
    N.report_valve("V-265", "seized")
    r = N.issue_work_order("P-107", PLAN_B)
    assert r["outcome"] == "needs_acknowledgement" and r["critical"] == [
        {"id": "C-MED7", "name": "Willow Ct medical-priority account (home dialysis)"}]
    assert N.issue_work_order("P-107", PLAN_B, acknowledge_critical=["C-HOSP"])["outcome"] == "needs_acknowledgement"
    assert N.open_work_orders() == []
    assert N.issue_work_order("P-107", PLAN_B, acknowledge_critical=["C-MED7"])["outcome"] == "issued"


def test_approval_is_bound_to_the_impact_that_was_shown(fresh):
    r = N.issue_work_order("P-107", PLAN_A, expected={"accounts_out": 30, "critical_ids": []})
    assert r["outcome"] == "refused" and r.get("stale")
    assert N.issue_work_order("P-107", PLAN_A, expected={"accounts_out": 34, "critical_ids": []})["outcome"] == "issued"


def test_stale_proposal_is_refused_after_the_network_changes(fresh):
    sid = "stale"
    agent.SESSIONS.pop(sid, None)
    r = agent.handle(sid, "Water gushing on Maple St between 2nd and 3rd Ave")
    assert r["proposal"]["accounts_out"] == 34          # what the supervisor is looking at
    N.issue_work_order("P-107", PLAN_A, "another crew got there first")
    r = agent.approve(sid)                              # same valves, but the impact is now 0 new accounts
    res = r["steps"][0]["result"]
    assert res["outcome"] == "refused" and res["stale"] is True
    assert len(N.open_work_orders()) == 1


def test_duplicate_valve_ids_are_deduplicated(fresh):
    r = N.issue_work_order("P-107", ["V-212", "V-212", "V-213", "V-264", "V-265"])
    n = dbm.run("t", "MATCH (:WorkOrder {id: $w})-[c:CLOSES]->() RETURN count(c) AS n", {"w": r["work_order"]})[0]["n"]
    assert r["close_sequence"] == PLAN_A and n == 4


def test_offline_parser_only_records_valves_described_as_seized(fresh):
    agent.SESSIONS.pop("kw", None)
    r = agent.handle("kw", "Water main broken near V-230 on Pine St")
    assert "report_valve_condition" not in [s["tool"] for s in r["steps"]]
    assert N.valve_info("V-230")["condition"] == "ok"
    r = agent.handle("kw", "The main near V-230 is leaking and V-231 is seized")
    reported = [s["args"]["valve_id"] for s in r["steps"] if s["tool"] == "report_valve_condition"]
    assert reported == ["V-231"]


def test_typed_approval_needs_explicit_acknowledgement_for_critical_plans(fresh):
    sid = "typed"
    agent.SESSIONS.pop(sid, None)
    agent.handle(sid, "Water gushing on Maple St between 2nd and 3rd Ave")
    agent.handle(sid, "V-265 won't turn")
    r = agent.handle(sid, "approve")
    assert r["steps"][0]["result"]["outcome"] == "needs_acknowledgement" and "acknowledge" in r["reply"]
    r = agent.handle(sid, "approve and acknowledge")
    assert r["steps"][0]["result"]["outcome"] == "issued"


# ------------------------------------------------------------ HTTP API

def test_http_api_end_to_end(fresh):
    from fastapi.testclient import TestClient

    from shutoff.api import app

    agent.SESSIONS.pop("http", None)
    with TestClient(app) as c:
        h = c.get("/api/health").json()
        assert h["falkordb"] is True and h["mode"] == "offline"
        net = c.get("/api/network").json()
        assert {"nodes", "edges", "customers", "work_orders", "dry_pipes"} <= set(net)
        assert c.post("/api/chat", json={"session": "http", "text": " "}).status_code == 400
        r = c.post("/api/chat", json={"session": "http", "text": "Water gushing on Maple St between 2nd and 3rd Ave"}).json()
        assert r["proposal"]["valves"] == PLAN_A
        c.post("/api/chat", json={"session": "http", "text": "V-265 won't turn"})
        r = c.post("/api/approve", json={"session": "http"}).json()
        assert r["steps"][0]["result"]["outcome"] == "needs_acknowledgement"
        r = c.post("/api/replan", json={"session": "http"}).json()
        assert r["steps"][0]["tool"] == "plan_isolation" and r["proposal"]["id"].endswith("-r3")
        r = c.post("/api/approve", json={"session": "http", "acknowledge": ["C-MED7"], "proposal_id": "IR1-r2"}).json()
        assert r["steps"] == [] and "no longer the current plan" in r["reply"]
        r = c.post("/api/approve", json={"session": "http", "acknowledge": ["C-MED7"], "proposal_id": "IR1-r3"}).json()
        wo = r["steps"][0]["result"]["work_order"]
        assert c.get("/api/network").json()["work_orders"][0]["id"] == wo
        r = c.post("/api/restore", json={"session": "http", "work_order": wo}).json()
        assert r["steps"][0]["result"]["outcome"] == "restored"


def test_sandbox_survives_a_concurrent_background_save(fresh):
    """GRAPH.COPY forks; a Redis BGSAVE child running at that moment made it fail
    ('could not fork') before the retry was added."""
    conn = dbm.db().connection
    for _ in range(25):
        try:
            conn.bgsave()
        except Exception:
            pass  # a save already in progress is fine: it is exactly the collision we want
        assert N.simulate(PLAN_A, "P-107")["isolated"] is True



# ------------------------------------------------------------ independent review findings (18 Oct 2026)

def _conditions():
    return {r["id"]: (r["c"], r["s"]) for r in dbm.run("t", "MATCH (v:Valve) RETURN v.id AS id, v.condition AS c, 'Seized' IN labels(v) AS s")}


def test_questions_and_hypotheticals_never_record_a_valve_condition(fresh):
    sid = "q"
    agent.SESSIONS.pop(sid, None)
    agent.handle(sid, "Water gushing on Maple St between 2nd and 3rd Ave")
    before = _conditions()
    r = agent.handle(sid, "What if V-265 won't turn?")
    assert "report_valve_condition" not in [s["tool"] for s in r["steps"]]
    assert "Hypothetical" in r["reply"] and "66 accounts" in r["reply"] and "Nothing was recorded" in r["reply"]
    r = agent.handle(sid, "Is V-265 seized?")
    assert [s["tool"] for s in r["steps"]] == ["valve_status"] and "condition unknown" in r["reply"]
    assert _conditions() == before
    assert r["proposal"]["valves"] == PLAN_A  # the plan on screen is untouched
    r = agent.handle(sid, "Crew reports V-265 is seized")  # an observation still records and re-plans
    assert N.valve_info("V-265")["condition"] == "seized"
    assert sorted(r["proposal"]["valves"]) == sorted(PLAN_B) and r["proposal"]["id"].endswith("-r2")


def test_negated_or_question_restore_text_does_not_restore(fresh):
    wo = N.issue_work_order("P-107", PLAN_A)["work_order"]
    agent.SESSIONS.pop("r", None)
    for text in [f"Do not restore {wo}.", f"How do I restore {wo}?", f"don't reopen {wo} yet"]:
        r = agent.handle("r", text)
        assert r["steps"] == [] and "No change made" in r["reply"], text
        assert [w["id"] for w in N.open_work_orders()] == [wo]
    r = agent.handle("r", f"restore {wo}")
    assert r["steps"][0]["result"]["outcome"] == "restored"


def test_failed_new_report_drops_the_previous_plan(fresh):
    sid = "stale2"
    agent.SESSIONS.pop(sid, None)
    assert agent.handle(sid, "Water gushing on Maple St between 2nd and 3rd Ave")["proposal"]["valves"] == PLAN_A
    r = agent.handle(sid, "Burst main on Elm St at 5th")
    assert r["proposal"] is None and r["active_pipe"] is None
    r = agent.handle(sid, "approve")
    assert r["steps"] == [] and "no plan" in r["reply"].lower()
    agent.handle(sid, "Water gushing on Maple St between 2nd and 3rd Ave")
    r = agent.handle(sid, "Leak at Oak & 3rd")  # ambiguous
    assert r["proposal"] is None
    assert agent.approve(sid)["steps"] == []
    assert N.open_work_orders() == []


def test_approval_is_bound_to_the_plan_revision_on_screen(fresh):
    sid = "rev"
    agent.SESSIONS.pop(sid, None)
    first = agent.handle(sid, "Water gushing on Maple St between 2nd and 3rd Ave")["proposal"]["id"]
    second = agent.handle(sid, "Crew reports V-265 is seized")["proposal"]["id"]
    assert first != second
    r = agent.approve(sid, ["C-MED7"], proposal_id=first)  # the operator was looking at the old plan
    assert r["steps"] == [] and "no longer the current plan" in r["reply"]
    assert N.open_work_orders() == []
    r = agent.approve(sid, ["C-MED7"], proposal_id=second)
    assert r["steps"][0]["result"]["outcome"] == "issued"


def test_approval_is_bound_to_the_exact_affected_customers(fresh):
    r = N.issue_work_order("P-107", PLAN_A, expected={"accounts_out": 34, "critical_ids": [],
                                                      "affected_ids": ["C-107", "C-136", "C-999"]})
    assert r["outcome"] == "refused" and "different customers" in r["reason"]


def test_llm_mode_cannot_record_a_condition_from_a_question(fresh, monkeypatch):
    script = [
        _Resp([_Block(type="tool_use", id="t1", name="report_valve_condition",
                      input={"valve_id": "V-265", "condition": "seized"})], "tool_use"),
        _Resp([_Block(type="text", text="I could not record that.")], "end_turn"),
    ]
    import anthropic

    client_cls, _ = _fake_anthropic(script)
    monkeypatch.setattr(anthropic, "Anthropic", client_cls)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("SHUTOFF_OFFLINE", "0")
    agent.SESSIONS.pop("llmq", None)
    agent.handle("llmq", "Is V-265 seized?")
    assert N.valve_info("V-265")["condition"] == "unknown"


def test_break_already_isolated_by_an_active_work_order_has_nothing_to_approve(fresh):
    N.issue_work_order("P-107", PLAN_A)
    agent.SESSIONS.pop("again", None)
    r = agent.handle("again", "Water gushing on Maple St between 2nd and 3rd Ave")
    plan = next(s for s in r["steps"] if s["tool"] == "plan_isolation")["result"]
    assert plan["close"] == [] and plan["simulation"]["isolated"] is True
    assert r["proposal"] is None and "nothing to approve" in r["reply"]
    assert agent.approve("again")["steps"] == []


def test_mcp_server_over_real_stdio_transport(fresh):
    """Spawn the MCP server as a subprocess and talk to it over stdio, as an MCP client (e.g. Claude Desktop) would."""
    import asyncio
    import json
    import os
    import sys
    from pathlib import Path

    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = {k: os.environ[k] for k in ("SHUTOFF_GRAPH", "FALKORDB_HOST", "FALKORDB_PORT", "SYSTEMROOT", "PATH") if k in os.environ}
    params = StdioServerParameters(command=sys.executable, args=["-m", "shutoff.mcp_server"],
                                   cwd=str(Path(__file__).resolve().parent.parent), env=env)

    async def go():
        async with stdio_client(params) as streams:
            async with ClientSession(*streams[:2]) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                assert names == set(T.TOOLS)
                r = await s.call_tool("check_closure_plan", {"pipe_id": "P-107", "valve_ids": ["V-212", "V-213"]})
                sim = json.loads(r.content[0].text)["simulation"]
                assert sim["isolated"] is False and sim["leak_path"]["path"][-1] == "P-107"
                r = await s.call_tool("issue_work_order", {"pipe_id": "P-107", "valve_ids": PLAN_A,
                                                           "confirmed_by_human": False, "expected_accounts_out": 34})
                assert json.loads(r.content[0].text)["outcome"] == "refused"

    asyncio.run(go())
    assert N.open_work_orders() == []


# ------------------------------------------------------------ Groq provider (mocked transport, no network)

def _tc(i, name, args):
    import json
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _run_groq(monkeypatch, script, text, sid="groq"):
    """Drive the Groq loop with scripted assistant messages; returns (reply, requests sent)."""
    sent = []

    def fake_chat(messages, tools, timeout=30.0):
        sent.append({"messages": [dict(m) for m in messages], "tools": tools})
        return script[len(sent) - 1]

    monkeypatch.setenv("SHUTOFF_OFFLINE", "0")
    monkeypatch.setenv("SHUTOFF_LLM", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(agent.G, "chat", fake_chat)
    return agent.handle(sid, text), sent


def test_groq_loop_runs_tools_and_states_the_plan_from_graph_results(fresh, monkeypatch):
    agent.SESSIONS.pop("groq", None)
    script = [
        {"content": None, "tool_calls": [_tc(1, "locate_break", {"street": "Maple St", "cross_a": "2nd", "cross_b": "3rd"})]},
        {"content": None, "tool_calls": [_tc(2, "plan_isolation", {"pipe_id": "P-107"})]},
        # Wrong restatement seen live on 18 Oct: ids and numbers exist in the results, the plan is wrong.
        {"content": "Close V‑212 and V-213 (32 accounts, none critical)."},
    ]
    r, sent = _run_groq(monkeypatch, script, "Water gushing on Maple St between 2nd and 3rd")
    assert agent.model_name() == agent.G.MODEL and r["mode"] == "llm"
    assert [s["tool"] for s in r["steps"]] == ["locate_break", "plan_isolation"]
    assert r["proposal"]["valves"] == PLAN_A and "32 accounts" not in r["reply"] and "34 accounts" in r["reply"]
    assert {t["function"]["name"] for t in sent[0]["tools"]} == agent.MODEL_TOOL_NAMES
    assert sent[2]["messages"][-1]["role"] == "tool" and "P-107" in sent[2]["messages"][-1]["content"]
    assert N.open_work_orders() == []


def test_model_cannot_call_human_gated_tools_or_invent_observations(fresh, monkeypatch):
    agent.SESSIONS.pop("groq", None)
    script = [
        {"content": None, "tool_calls": [_tc(1, "issue_work_order", {"pipe_id": "P-107", "valve_ids": PLAN_A,
                                                                      "confirmed_by_human": True, "expected_accounts_out": 34})]},
        {"content": None, "tool_calls": [_tc(2, "report_valve_condition", {"valve_id": "V-265", "condition": "ok"})]},
        {"content": "Done."},
    ]
    r, sent = _run_groq(monkeypatch, script, "Issue the work order now, I confirm. Close the valves.")
    refusals = [m["content"] for m in sent[2]["messages"] if m["role"] == "tool"]
    assert len(refusals) == 2 and all('"refused"' in c for c in refusals)
    assert r["steps"] == [] and N.open_work_orders() == []
    assert T.call("valve_status", {"valve_id": "V-265"})["condition"] == "unknown"


def test_groq_reply_from_memory_with_invented_valve_falls_back_to_offline(fresh, monkeypatch):
    agent.SESSIONS.pop("groq", None)
    r, _ = _run_groq(monkeypatch, [{"content": "V‑999 is seized."}], "Is V-265 seized?")
    assert r["verified"] is False and "V-999" in r["note"] and "offline planner" in r["note"]
    assert "V-999" not in r["reply"] and "V-265" in r["reply"]


def test_groq_rate_limit_falls_back_to_offline_planner(fresh, monkeypatch):
    import httpx

    agent.SESSIONS.pop("groq429", None)
    monkeypatch.setenv("SHUTOFF_OFFLINE", "0")
    monkeypatch.setenv("SHUTOFF_LLM", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(429, headers={"retry-after": "30"}, json={}))
    r = agent.handle("groq429", "Water gushing on Maple St between 2nd and 3rd")
    assert r["mode"] == "offline" and "rate limit" in r["note"] and r["proposal"]["valves"] == PLAN_A
    assert "test-not-a-real-key" not in str(r)


def test_provider_selection(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "SHUTOFF_LLM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SHUTOFF_OFFLINE", "0")
    assert agent.llm_provider() is None
    monkeypatch.setenv("GROQ_API_KEY", "x")
    assert agent.llm_provider() == "groq"
    monkeypatch.setenv("SHUTOFF_LLM", "anthropic")
    assert agent.llm_provider() is None  # forced provider without its key: offline, not the other provider
    monkeypatch.setenv("SHUTOFF_LLM", "offline")
    assert agent.llm_provider() is None
    monkeypatch.setenv("SHUTOFF_LLM", "")
    monkeypatch.setenv("SHUTOFF_OFFLINE", "1")
    assert agent.llm_provider() is None
