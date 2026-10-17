"""Tests that need no database."""

from shutoff import agent, town
from shutoff import tools as T
from shutoff.agent import Step, check_reply


def test_town_is_consistent():
    net = town.build()
    ids = {n["id"] for k in ("sources", "junctions", "pipes", "valves") for n in net[k]}
    assert len(ids) == sum(len(net[k]) for k in ("sources", "junctions", "pipes", "valves")), "duplicate ids"
    for a, b in net["links"]:
        assert a in ids and b in ids
    # every pipe is reached from exactly two ends (directly or through a valve)
    for p in net["pipes"]:
        degree = sum(1 for a, b in net["links"] if p["id"] in (a, b))
        assert degree == 2, p
    # every valve sits between one end node and one pipe
    for v in net["valves"]:
        assert sum(1 for a, b in net["links"] if v["id"] in (a, b)) == 2


def test_demo_area_has_the_missing_valves_the_scenario_relies_on():
    net = town.build()
    p107 = net["pipe_id_by_key"]["H-1-1"]
    valves_on_p107 = [v for v in net["valves"] if v["pipe"] == p107]
    assert len(valves_on_p107) == 1  # only the 2nd Ave end has a valve


def test_tool_argument_validation_rejects_bad_ids_without_touching_the_db():
    assert T.call("plan_isolation", {"pipe_id": "P-107; MATCH (n) DETACH DELETE n"})["outcome"] == "invalid_arguments"
    assert T.call("check_closure_plan", {"pipe_id": "P-107", "valve_ids": ["V-1 OR 1=1"]})["outcome"] == "invalid_arguments"
    assert T.call("report_valve_condition", {"valve_id": "V-265", "condition": "melted"})["outcome"] == "invalid_arguments"
    assert T.call("restore_service", {"work_order_id": "DROP"})["outcome"] == "invalid_arguments"
    assert T.call("no_such_tool", {})["outcome"] == "error"


def test_issue_work_order_requires_human_confirmation():
    r = T.call("issue_work_order", {"pipe_id": "P-107", "valve_ids": ["V-212"]})
    assert r["outcome"] == "refused"


def test_model_is_not_offered_human_gated_tools():
    names = {t["name"] for t in T.model_tools()}
    assert "issue_work_order" not in names and "restore_service" not in names
    assert {"locate_break", "plan_isolation", "check_closure_plan"} <= names


def test_reply_check_rejects_invented_ids_and_counts():
    steps = [Step("plan_isolation", {}, {"close": [{"id": "V-212"}], "simulation": {"accounts_out": 34}}, [], 1.0)]
    assert check_reply("Close V-212: 34 accounts lose water.", steps) == (True, None)
    ok, why = check_reply("Close V-212 and V-999.", steps)
    assert not ok and "V-999" in why
    ok, why = check_reply("Close V-212: 40 accounts lose water.", steps)
    assert not ok and "40" in why


def test_summary_marks_refusals_and_missing_data():
    assert agent.summarize("locate_break", {"outcome": "not_in_graph", "reason": "Street 'Elm St' is not in the network graph."}).startswith("Not in the graph")
    assert agent.summarize("issue_work_order", {"outcome": "refused", "reason": "x"}).startswith("Refused")
