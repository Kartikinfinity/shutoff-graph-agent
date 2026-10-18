"""The ShutOff agent: turns a dispatcher's or crew's message into tool calls.

Two planners share the same tools and the same evidence trace:

* LLM mode (when ANTHROPIC_API_KEY is set): Claude chooses tools and writes
  a short reply. The reply is checked against what the graph returned; any
  valve/pipe id or account count the graph did not produce gets the reply
  rejected and replaced by a summary built from the tool results.
* Offline mode (default, deterministic): a small intent parser maps the
  message to the same tools. Used for the demo and the tests.

Committing a shutdown (closing valves) is never a model decision: it happens
only when a human approves the current proposal.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

from . import db as dbm
from . import network as N
from . import tools as T

MODEL = os.getenv("SHUTOFF_MODEL", "claude-opus-5-5")

SYSTEM_PROMPT = """You are ShutOff, the isolation assistant for the Riverbend water utility's shift supervisor.
A synthetic network graph (FalkorDB) is the only source of truth about pipes, valves and customers.

When a main break is reported: locate it, then plan the isolation, and explain the plan.
When a crew reports an observation that a valve will not turn: record it as seized, then re-plan the active break.
Questions ("is V-265 seized?") and what-if scenarios ("what if V-265 won't turn?") are NOT observations: answer them
with valve_status or the plan's contingencies and record nothing.
If someone proposes their own closure plan, check it before agreeing.
Use tools for every fact. Never invent valve, pipe or customer names or counts; only use what tools returned.
If a location is not in the graph or is ambiguous, say so and ask; do not guess.
You cannot close or re-open valves yourself: the supervisor approves or restores with buttons.
Everything you report is a simulation on a synthetic network: say "would", not "is", for proposed effects.
Reply in at most 4 short sentences, plain text. Lead with the decision (which valves, how many accounts out),
then any critical customer at risk and what to watch for."""

ID_RE = re.compile(r"\b(?:V|P|WO|INC)-\d{3,4}(?:-\d+)?\b")
COUNT_RE = re.compile(r"\b(\d+)\s+(?:accounts?|customers?|homes?|connections?)\b", re.I)


@dataclass
class Step:
    tool: str
    args: dict
    result: dict
    queries: list
    ms: float


@dataclass
class Session:
    active_pipe: str | None = None
    report: str = ""
    proposal: dict | None = None        # the plan on screen; approval is bound to proposal["id"]
    last_plan: dict | None = None       # full plan_isolation result behind the proposal
    incident_seq: int = 0
    rev: int = 0
    messages: list = field(default_factory=list)  # LLM conversation (LLM mode only)


SESSIONS: dict[str, Session] = {}


def session(sid: str) -> Session:
    return SESSIONS.setdefault(sid, Session())


def llm_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("SHUTOFF_OFFLINE") != "1"


# ------------------------------------------------------------------ running

def _run_tool(s: Session, name: str, args: dict, steps: list[Step]) -> dict:
    tr = dbm.start_trace()
    t0 = time.perf_counter()
    result = T.call(name, args)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    steps.append(Step(name, args, result, [q.__dict__ for q in tr.queries], ms))
    _update_state(s, name, args, result)
    return result


def _update_state(s: Session, name: str, args: dict, result: dict) -> None:
    if name == "locate_break":
        # A new report opens a new incident context. Whatever happens next, the
        # previous plan is no longer approvable.
        s.incident_seq += 1
        s.rev = 0
        s.proposal = None
        s.last_plan = None
        s.active_pipe = result["pipe"] if result.get("outcome") == "found" else None
    if name == "plan_isolation":
        if result.get("outcome") == "planned":
            s.active_pipe = result["pipe"]["id"]
            s.rev += 1
            sim = result["simulation"]
            s.last_plan = result
            s.proposal = {"id": f"IR{max(s.incident_seq, 1)}-r{s.rev}", "rev": s.rev,
                          "pipe": result["pipe"]["id"], "pipe_label": result["pipe"]["label"],
                          "valves": [v["id"] for v in result["close"]],
                          "accounts_out": sim["accounts_out"],
                          "critical_out": [c["name"] for c in sim["critical_out"]],
                          "critical_ids": sorted({c["id"] for c in sim["critical_out"]}),
                          "affected_ids": sorted({c["id"] for c in sim["intended"] + sim["unintended"]})}
        else:  # cannot isolate / unknown pipe: nothing on screen may be approved
            s.proposal = None
            s.last_plan = None
    if name == "issue_work_order" and result.get("outcome") == "issued":
        s.proposal = None


def handle(sid: str, text: str) -> dict:
    """One user turn. Returns the reply, the evidence steps and the proposal."""
    s = session(sid)
    text = (text or "").strip()[:500]
    steps: list[Step] = []
    approval = _approval_intent(s, text, steps)
    if approval is not None:
        reply, mode, verified, note = approval, "human-action", True, None
    elif llm_available():
        try:
            reply, verified, note = _llm_turn(s, text, steps)
            mode = "llm"
        except Exception as e:  # network/API failure: fall back, and say so
            steps.clear()
            reply = _offline_turn(s, text, steps)
            mode, verified, note = "offline", True, f"LLM unavailable ({type(e).__name__}); used offline planner."
    else:
        reply = _offline_turn(s, text, steps)
        mode, verified, note = "offline", True, None
    return {"reply": reply, "mode": mode, "verified": verified, "note": note,
            "steps": [st.__dict__ for st in steps], "proposal": s.proposal, "active_pipe": s.active_pipe}


def _issue_args(s: Session, acknowledge: list[str] | None) -> dict:
    p = s.proposal
    return {"pipe_id": p["pipe"], "valve_ids": p["valves"], "report": s.report, "confirmed_by_human": True,
            "acknowledge_critical": acknowledge or [],
            "expected_accounts_out": p["accounts_out"], "expected_critical_ids": p["critical_ids"],
            "expected_affected_ids": p["affected_ids"]}


def _reply(s: Session, reply: str, steps: list[Step], mode: str = "human-action") -> dict:
    return {"reply": reply, "mode": mode, "verified": True, "note": None,
            "steps": [st.__dict__ for st in steps], "proposal": s.proposal, "active_pipe": s.active_pipe}


def approve(sid: str, acknowledge: list[str] | None = None, proposal_id: str | None = None) -> dict:
    """The supervisor pressed Approve: commit the current proposal, bound to the
    plan revision on their screen, the impact they were shown and the critical
    customers they acknowledged."""
    s = session(sid)
    steps: list[Step] = []
    if not s.proposal:
        return _reply(s, "There is no plan to approve. Report or re-plan the break first.", steps)
    if proposal_id is not None and proposal_id != s.proposal["id"]:
        return _reply(s, f"Not issued: the plan on screen ({proposal_id}) is no longer the current plan "
                         f"({s.proposal['id']}). Review the current plan before approving.", steps)
    r = _run_tool(s, "issue_work_order", _issue_args(s, acknowledge), steps)
    return {"reply": summarize("issue_work_order", r), "mode": "human-action", "verified": True, "note": None,
            "steps": [st.__dict__ for st in steps], "proposal": s.proposal, "active_pipe": s.active_pipe}


def restore(sid: str, wo: str) -> dict:
    s = session(sid)
    steps: list[Step] = []
    r = _run_tool(s, "restore_service", {"work_order_id": wo, "confirmed_by_human": True}, steps)
    return {"reply": summarize("restore_service", r), "mode": "human-action", "verified": True, "note": None,
            "steps": [st.__dict__ for st in steps], "proposal": s.proposal, "active_pipe": s.active_pipe}


def _approval_intent(s: Session, text: str, steps: list[Step]) -> str | None:
    """Typed human commands that commit actions are handled here, never by the model."""
    low = text.lower()
    m = re.fullmatch(r"\s*(?:please\s+)?(?:restore|re-?open)\s+(?:service\s+(?:for\s+)?)?(WO-\d{4})\s*[.!]?\s*", text, re.I)
    if m:
        r = _run_tool(s, "restore_service", {"work_order_id": m.group(1).upper(), "confirmed_by_human": True}, steps)
        return summarize("restore_service", r)
    if re.search(r"\b(?:restore|re-?open)\b", low) and re.search(r"\bWO-\d{4}\b", text, re.I):
        return ("No change made. To restore service, press Restore on the work order, or type exactly "
                "'restore WO-1001' (with the right number).")
    m = re.fullmatch(r"\s*(?:approve[d]?|go ahead|issue (?:the )?work order|confirm)"
                     r"(?:\s*(?:,|and)?\s*(acknowledge[d]?)(?: (?:the )?critical(?: customers?)?)?)?\W*", low)
    if m:
        if not s.proposal:
            return "There is no plan to approve yet."
        ack = s.proposal["critical_ids"] if m.group(1) else []
        r = _run_tool(s, "issue_work_order", _issue_args(s, ack), steps)
        return summarize("issue_work_order", r)
    return None


# ------------------------------------------------------------------ offline

LANDMARKS = ["hospital", "school", "elementary", "bakery", "senior", "oakview", "dialysis", "willow ct"]
SEIZED_WORDS = (r"(?:seized|stuck|jammed|frozen|inoperable|won'?t (?:turn|close|move|budge)|"
                r"will not (?:turn|close|move)|can'?t (?:turn|close|move)|cannot (?:turn|close|move))")
OK_WORDS = r"(?:freed|working|works|turns|turned|operable|fixed|ok|okay|fine)"
_VLIST = r"(V-\d{3}(?:\s*(?:,|and|&)\s*V-\d{3})*)"
_FILLER = r"\s*(?:valves?\s*)?(?:,?\s*(?:it|they|which)(?:'s|\s+is|\s+are)?\s*)?(?:is|are|was|were|has|have|seems|looks)?\s*(?:still\s+|now\s+|completely\s+|totally\s+)?(?:been\s+)?"


def _valves_described(text: str, words: str) -> list[str]:
    """Valve ids the sentence actually says are `words` (e.g. 'V-265 won't turn',
    'V-212 and V-213 are stuck', 'stuck valve V-265'). A valve id merely
    mentioned near the word ('main broken near V-230') does not count."""
    found: list[str] = []
    for pat in (_VLIST + _FILLER + words + r"\b", r"\b" + words + r"\s*(?:valves?\s*)?:?\s*" + _VLIST):
        for m in re.finditer(pat, text, re.I):
            for v in re.findall(r"V-\d{3}", m.group(1), re.I):
                if v.upper() not in found:
                    found.append(v.upper())
    return found


def _street_mentions(text: str) -> list[str]:
    streets = N.known_streets() + ["Willow Ct"]
    words = {}
    for st in streets:
        first = N._norm(st).split()[0]
        if first not in ("reservoir", "tank"):
            words[first] = st
    found = []
    norm = N._norm(text)
    for tok in re.findall(r"[a-z0-9]+", norm):
        if tok in words and words[tok] not in found:
            found.append(words[tok])
    return found


QUESTION_START = r"^\s*(?:what|how|is|are|was|were|can|could|should|would|will|do|does|did|why|when|where|which|who|has|have|shall|may|might)\b"
HYPOTHETICAL = r"\b(?:what if|if|suppose|supposing|assume|assuming|in case|hypothetically|imagine|would|could|might)\b"


def is_question(text: str) -> bool:
    return text.strip().endswith("?") or bool(re.search(QUESTION_START, text, re.I))


def is_hypothetical(text: str) -> bool:
    return bool(re.search(HYPOTHETICAL, text, re.I))


def is_observation(text: str) -> bool:
    """A field report the graph may record: a statement, not a question or a what-if."""
    return not is_question(text) and not is_hypothetical(text)


def _valve_question(s: Session, text: str, valves: list[str], steps: list[Step]) -> str:
    """Questions and what-ifs about valves are answered read-only."""
    out = []
    for v in valves:
        cont = None
        if s.last_plan and is_hypothetical(text):
            cont = next((c for c in s.last_plan["contingencies"] if c["if_fails"] == v), None)
        if cont:
            base = s.last_plan["simulation"]["accounts_out"]
            if not cont["isolated"]:
                out.append(f"Hypothetical: if {v} cannot close, no operable valves would isolate the break.")
            else:
                out.append(f"Hypothetical (from the current plan's contingency simulation): if {v} cannot close, the "
                           f"plan grows to {len(cont['close_instead'])} valves and {cont['accounts_out']} accounts "
                           f"(+{cont['accounts_out'] - base})"
                           + (f", cutting off {', '.join(cont['critical_out'])}" if cont["critical_out"] else "") + ".")
        else:
            r = _run_tool(s, "valve_status", {"valve_id": v}, steps)
            out.append(summarize("valve_status", r))
    return " ".join(out) + (" Nothing was recorded. To record a crew observation, state it, e.g. "
                            "'Crew reports V-265 is seized'.")


def _offline_turn(s: Session, text: str, steps: list[Step]) -> str:
    low = text.lower()
    valves = [v.upper() for v in re.findall(r"\bV-\d{3}\b", text, re.I)]

    # 0. Questions and what-ifs about valves never write anything.
    if valves and (is_question(text) or is_hypothetical(text)) and not re.search(r"\b(close|shut|closing|shutting)\b", low):
        return _valve_question(s, text, valves, steps)

    # 1. Field report about a valve (an observation, never a question or hypothesis).
    seized = _valves_described(text, SEIZED_WORDS) if is_observation(text) else []
    if seized:
        out = []
        for v in seized:
            r = _run_tool(s, "report_valve_condition", {"valve_id": v, "condition": "seized", "note": text}, steps)
            out.append(summarize("report_valve_condition", r))
        if s.active_pipe:
            r = _run_tool(s, "plan_isolation", {"pipe_id": s.active_pipe}, steps)
            out.append("Re-planned: " + summarize("plan_isolation", r))
        return " ".join(out)
    freed = [] if re.search(r"\b(close|shut)\b", low) or not is_observation(text) else _valves_described(text, OK_WORDS)
    if freed:
        out = []
        for v in freed:
            r = _run_tool(s, "report_valve_condition", {"valve_id": v, "condition": "ok", "note": text}, steps)
            out.append(summarize("report_valve_condition", r))
        if s.active_pipe:
            r = _run_tool(s, "plan_isolation", {"pipe_id": s.active_pipe}, steps)
            out.append("Re-planned: " + summarize("plan_isolation", r))
        return " ".join(out)

    # 2. Someone proposes their own closure plan.
    if valves and re.search(r"\b(close|shut|closing|shutting)\b", low):
        if not s.active_pipe:
            return "Which break is this plan for? Report the break location first."
        r = _run_tool(s, "check_closure_plan", {"pipe_id": s.active_pipe, "valve_ids": valves}, steps)
        return summarize("check_closure_plan", r)

    # 3. Preventive maintenance ranking.
    if re.search(r"\b(exercis\w*|maintenance|priorit\w*|which valves)\b", low):
        r = _run_tool(s, "valve_exercise_priorities", {"limit": 6}, steps)
        return summarize("valve_exercise_priorities", r)

    # 4. History of the active pipe.
    if re.search(r"\b(history|before|previous|past)\b", low) and s.active_pipe:
        r = _run_tool(s, "pipe_history", {"pipe_id": s.active_pipe}, steps)
        return summarize("pipe_history", r)

    # 5. Supply check for a customer.
    m = re.search(r"\b(?:is|are|does|check)\s+(?:the\s+)?(.+?)\s+(?:still\s+)?(?:ok|okay|supplied|safe|getting water|have water|in service)\b", low)
    if m or re.search(r"\bsupply\b", low):
        who = m.group(1) if m else next((l for l in LANDMARKS if l in low), "")
        if who:
            r = _run_tool(s, "supply_check", {"customer": who}, steps)
            msg = summarize("supply_check", r)
            if s.last_plan and r.get("outcome") in ("supplied", "no_supply"):
                st = next((c for c in s.last_plan["simulation"]["critical_status"] if c["pipe"] == r["pipe"]), None)
                if st:
                    msg += (f" Under proposed plan {s.proposal['id'] if s.proposal else ''}: "
                            + ("a source path would remain." if st["supplied"] else "it would lose its source connection.")).replace("  ", " ")
            return msg

    # 6. A break report.
    for word, kind in re.findall(r"\b([a-z0-9]+)\s+(st|street|ave|avenue|rd|road|ct|court|ln|lane|blvd)\b", low):
        if not N.resolve_street(f"{word} {kind}"):
            r = _run_tool(s, "locate_break", {"street": f"{word.title()} {kind.title()}"}, steps)
            return summarize("locate_break", r)
    mentions = _street_mentions(text)
    landmark = next((l for l in LANDMARKS if l in low), None)
    if mentions or (landmark and re.search(r"\b(near|by|outside|behind|at)\b", low)):
        s.report = text
        if len(mentions) >= 3:
            args = {"street": mentions[0], "cross_a": mentions[1], "cross_b": mentions[2]}
        elif len(mentions) == 2:
            args = {"street": mentions[0], "cross_a": mentions[1]}
        elif len(mentions) == 1 and not landmark:
            args = {"street": mentions[0]}
        else:
            args = {"street": "", "landmark": landmark}
        r = _run_tool(s, "locate_break", args, steps)
        if r.get("outcome") != "found":
            return summarize("locate_break", r)
        r = _run_tool(s, "plan_isolation", {"pipe_id": r["pipe"]}, steps)
        return summarize("plan_isolation", r)

    if re.search(r"\b(elm|main st|broadway|park)\b", low) or re.search(r"\b(st|street|ave|avenue|road)\b", low):
        r = _run_tool(s, "locate_break", {"street": text}, steps)
        return summarize("locate_break", r)

    return ("I can locate a main break and plan its isolation (e.g. 'Water gushing on Maple St between 2nd and "
            "3rd'), record a seized valve ('V-265 won't turn'), check a proposed plan ('can we just close V-212 "
            "and V-213?'), check a customer's supply ('is the hospital still supplied?'), or rank valves to "
            "exercise.")


# ------------------------------------------------------------------ summaries

def _names(cs: list[dict]) -> str:
    return ", ".join(c["name"] for c in cs)


def summarize(tool: str, r: dict) -> str:
    """Plain-English sentences built only from tool results."""
    o = r.get("outcome")
    if o == "needs_acknowledgement":
        return ("Not issued: " + r["reason"] + " Tick the acknowledgement in the plan panel, or type "
                "'approve and acknowledge'.")
    if o in ("not_in_graph", "invalid_arguments", "error", "refused"):
        extra = ""
        if r.get("leak_path"):
            lp = r["leak_path"]
            extra = f" Water would still reach it from {lp['source']} via {' > '.join(lp['path'][-5:])}."
        return f"{'Refused' if o == 'refused' else 'Not in the graph' if o == 'not_in_graph' else 'Cannot do that'}: {r.get('reason')}{extra}"
    if tool == "locate_break":
        if o == "ambiguous":
            return r["reason"] + " Candidates: " + "; ".join(f"{c['pipe']} ({c['label']})" for c in r["candidates"]) + "."
        return f"Break located on {r['label']} ({r['pipe']})."
    if tool == "plan_isolation":
        sim = r["simulation"]
        pipe = r["pipe"]
        valves = ", ".join(v["id"] for v in r["close"])
        parts = [f"Break on {pipe['label']} ({pipe['id']}). Proposed: close {valves}. In simulation "
                 f"{sim['accounts_out']} accounts would lose water"]
        if sim["unintended"]:
            parts[0] += f", including {sum(c['accounts'] for c in sim['unintended'])} cut off outside the shut-off area ({_names(sim['unintended'])})"
        parts[0] += "."
        parts.append("Simulation on a copy of the graph: the break would be disconnected from every source." if sim["isolated"]
                     else "WARNING: in simulation the break would still be connected to a source.")
        if sim.get("already_out_accounts"):
            parts.append(f"({sim['already_out_accounts']} accounts are already out under other work orders; not counted above.)")
        if sim["critical_out"]:
            parts.append(f"CRITICAL: {_names(sim['critical_out'])} would lose its source connection; "
                         "explicit acknowledgement is required before approval.")
        else:
            parts.append("No critical customer would lose its source connection.")
        risky = [c for c in r["contingencies"] if c["extra_accounts"] > 0 or c["critical_out"]]
        for w in r["warnings"]:
            parts.append(w)
        for c in r["contingencies"]:
            if not c["isolated"]:
                parts.append(f"If {c['if_fails']} will not turn, there is no backup isolation with operable valves.")
        for c in risky:
            if c["isolated"] and c["critical_out"] and c["critical_out"] != [x["name"] for x in sim["critical_out"]]:
                parts.append(f"If {c['if_fails']} will not turn, the plan grows to {len(c['close_instead'])} valves and "
                             f"{c['accounts_out']} accounts, and cuts off {', '.join(c['critical_out'])}.")
        if len(r.get("history") or []) >= 2:
            parts.append(f"This main ({pipe['material']}, {pipe['installed']}) has {len(r['history'])} earlier breaks on record.")
        return " ".join(parts)
    if tool == "check_closure_plan":
        sim = r["simulation"]
        if sim["isolated"]:
            return (f"In simulation, closing {', '.join(r['proposed'])} would disconnect the break; "
                    f"{sim['accounts_out']} accounts would lose water"
                    + (f", including critical: {_names(sim['critical_out'])}." if sim["critical_out"] else "."))
        lp = sim["leak_path"]
        path = " > ".join(lp["path"]) if lp else "unknown path"
        return (f"No. With only {', '.join(r['proposed'])} closed, the graph still connects the break to "
                f"{lp['source'] if lp else 'a source'} along {path}.")
    if tool == "report_valve_condition":
        return f"Crew observation saved in the graph: {r['valve']} ({r['location']}) is {r['condition']}."
    if tool == "valve_status":
        return (f"{r['valve']} ({r['location']}): recorded {r['status']}, condition {r['condition']}"
                f" (last exercised {r['last_exercised']}).")
    if tool == "supply_check":
        if o == "supplied":
            p = r["path"]
            return (f"Current graph (closures already in effect): {r['customer']} has a source path from "
                    f"{p['source']} ({int(p['hops'])} hops). This is connectivity, not a pressure guarantee.")
        return f"Current graph: {r['customer']} has NO path to any source."
    if tool == "valve_exercise_priorities":
        rows = r["ranked"]
        lines = [f"{x['valve']} ({x['location']}, last exercised {x['last_exercised']}, {x['condition']}): "
                 f"{x['accounts_out_if_seized']} accounts"
                 + (f" incl. {', '.join(x['critical_out_if_seized'])}" if x["critical_out_if_seized"] else "")
                 for x in rows[:5]]
        return f"Exercise these first ({r['valves_evaluated']} valves simulated): " + "; ".join(lines) + "."
    if tool == "pipe_history":
        inc = r["incidents"]
        if not inc:
            return f"No incidents recorded on {r['pipe']['label']}."
        return (f"{r['pipe']['label']} ({r['pipe']['material']}, {r['pipe']['installed']}): " +
                "; ".join(f"{i['id']} {i['at']} {i['summary']}" for i in inc) + ".")
    if tool == "issue_work_order":
        msg = (f"{r['work_order']} recorded for {r['incident']}: valves {', '.join(r['close_sequence'])} marked closed "
               f"in the graph; {r['accounts_out']} accounts affected; {r['standard_notices']} standard notices recorded")
        if r["priority_notices"]:
            msg += f"; PRIORITY notice recorded for {', '.join(r['priority_notices'])}"
        return msg + f". ({r['dispatch']}.)"
    if tool == "restore_service":
        msg = f"{r['work_order']} restored and {r['incident']} resolved. Re-opened: {', '.join(r['reopened']) or 'none'}."
        for k in r["kept_closed"]:
            msg += f" Kept {k['valve']} closed ({k['reason']}: {', '.join(k['held_by'])})."
        return msg
    return json.dumps(r)[:400]


# ------------------------------------------------------------------ LLM mode

def _compact(result: dict) -> str:
    """Tool result for the model: drop map-only noise, keep evidence."""
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in ("x", "y", "ax", "ay", "bx", "by", "queries")}
        if isinstance(x, list):
            return [strip(v) for v in x[:40]]
        return x
    return json.dumps(strip(result), default=str)[:12000]


def _result_numbers(x, acc: set[str]) -> None:
    """Integers that are actual result values (not digits inside timestamps or ids),
    plus account totals over customer lists."""
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)) and float(x).is_integer():
        acc.add(str(int(x)))
    elif isinstance(x, dict):
        for v in x.values():
            _result_numbers(v, acc)
    elif isinstance(x, list):
        for v in x:
            _result_numbers(v, acc)
        accts = [c.get("accounts") for c in x if isinstance(c, dict) and isinstance(c.get("accounts"), int)]
        if accts:
            acc.add(str(sum(accts)))
        acc.add(str(len(x)))


def check_reply(reply: str, steps: list[Step]) -> tuple[bool, str | None]:
    """Reject replies that mention ids or account counts the graph never returned."""
    evidence = json.dumps([st.result for st in steps], default=str)
    numbers: set[str] = set(COUNT_RE.findall(evidence))  # e.g. '(20 homes)' inside customer names
    for st in steps:
        _result_numbers(st.result, numbers)
    bad_ids = [i for i in ID_RE.findall(reply) if i not in evidence]
    bad_counts = [n for n in COUNT_RE.findall(reply) if n not in numbers]
    if bad_ids or bad_counts:
        return False, f"model reply mentioned {', '.join(bad_ids + bad_counts)} not returned by the graph"
    return True, None


def _llm_turn(s: Session, text: str, steps: list[Step]) -> tuple[str, bool, str | None]:
    import anthropic

    client = anthropic.Anthropic()
    user_text = text
    if s.active_pipe:
        text = f"{text}\n\n[context: active break pipe {s.active_pipe}]"
    s.messages.append({"role": "user", "content": text})
    s.report = s.report or text
    reply = ""
    for _ in range(8):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=T.model_tools(),
            messages=s.messages,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        s.messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "refusal":
            reply = "The model declined this request."
            break
        uses = [b for b in response.content if b.type == "tool_use"]
        if not uses:
            reply = " ".join(b.text for b in response.content if b.type == "text").strip()
            break
        results = []
        for b in uses:
            if b.name == "report_valve_condition" and not is_observation(user_text):
                r = {"outcome": "refused", "reason": ("Not recorded: the user's message is a question or a what-if, "
                                                      "not a crew observation. Use valve_status or the plan's contingencies.")}
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(r), "is_error": True})
                continue
            r = _run_tool(s, b.name, dict(b.input), steps)
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": _compact(r),
                            "is_error": r.get("outcome") in ("invalid_arguments", "error")})
        s.messages.append({"role": "user", "content": results})
    ok, why = check_reply(reply, steps)
    if not ok or not reply:
        fallback = " ".join(summarize(st.tool, st.result) for st in steps) or "No answer from the model."
        return fallback, False, why or "model returned no text; reply built from tool results"
    return reply, True, None
