# ShutOff — independent review handoff

Baseline under review: git commit **`54e5e78`** on branch `master` (17 Oct 2026, 19:39 IST).
Pre-audit baseline: `832a626`. Event: Graph Hacks (WeMakeDevs × FalkorDB), Track 1 "Agents That Act on Connected Data".
State of this baseline: 40/40 tests pass, the app starts, and the live Claude API mode is **not** tested (see §4).

> **Update, 18 Oct 2026.** This handoff describes `54e5e78`. Since then an independent review was addressed (see
> [RESEARCH_AND_REDESIGN.md](RESEARCH_AND_REDESIGN.md) §6):
> - Questions, what-ifs and negations no longer trigger writes.
> - Plans carry revision IDs; approval is bound to the revision on screen and to the exact affected customers.
> - A failed report clears the old plan.
> - MCP restore and issue need confirmation plus the shown impact.
> - New read-only `valve_status` tool and `/api/replan` endpoint.
> - The UI was redesigned around the operator workflow.
> - ShutOff's segment query was cross-checked against WNTR on public EPANET networks ([docs/BENCHMARK_WNTR.md](docs/BENCHMARK_WNTR.md)).
>
> Tests: 50 core + 3 optional benchmark tests. File list additions: `benchmarks/`, `tests/test_benchmark.py`,
> `requirements-benchmark.txt`, `RESEARCH_AND_REDESIGN.md`.

---

## 1. What it is, for whom, and the problem

**Who:** the shift supervisor or dispatcher at a water utility, plus the field crew radioing in.

**Problem:** when a water main breaks, someone must decide quickly which valves to close.
- Valves isolate a *segment* of the network.
- A valve that won't turn (seized) silently enlarges the shutdown.
- Closing a segment can also cut off customers *outside* it whose only supply ran through it ("unintended isolation").

The segment and unintended-isolation concepts come from the water-distribution literature (e.g. Jun, Loganathan et al., *Water Resources Management* 22(6), 2008). The network is a looped graph, so these are graph-connectivity questions.

**What ShutOff does:**
1. Turns a free-text break report into a located pipe.
2. Computes the isolation plan with FalkorDB graph algorithms.
3. Simulates it on a throwaway copy of the graph and proves the result with paths.
4. Pre-computes backup plans for each valve that might fail.
5. Checks plans that humans propose.
6. Records field reports (seized valves) in the graph.
7. After human approval, writes a work order that closes valves in the graph.
8. Restores service safely.

Crew dispatch and customer notices are **simulated** (recorded as graph edges only).

**Data:** the "Riverbend" network is entirely synthetic (`shutoff/town.py`): a 5 × 6 street grid, one reservoir, one tank, and one dead-end cul-de-sac (Willow Ct) holding a medical-priority (home dialysis) account.

---

## 2. Architecture and end-to-end flow

```
Browser UI (web/)  --HTTP-->  FastAPI  shutoff/api.py
                                 |
                       shutoff/agent.py   offline intent parser (default) | Claude tool loop (only with ANTHROPIC_API_KEY)
MCP client --stdio/HTTP-->  shutoff/mcp_server.py
                                 |
                       shutoff/tools.py   fixed tool catalogue, argument validation, human-gated actions
                                 |
                       shutoff/network.py planning, sandbox simulation, contingencies, actions
                                 |
                       shutoff/queries.py every Cypher query  -->  shutoff/db.py (tracer)  -->  FalkorDB
                                                                  live graph "riverbend" + throwaway sandboxes
```

**One request, end to end** ("Water gushing on Maple St between 2nd and 3rd Ave"):

1. **Input.** `POST /api/chat` reaches `agent.handle()`. Typed approval or restore commands are intercepted first (`_approval_intent`); the model never handles them. The offline parser finds street mentions (`_street_mentions`) and calls tool `locate_break`.
2. **Resolution.** `network.locate_break` normalises names in Python, then runs `STREET_NAMES` and `PIPE_BETWEEN`. The result is `P-107`. Unknown streets return `not_in_graph`; several matching mains return `ambiguous` with candidates. Nothing is guessed.
3. **Planning.** Tool `plan_isolation` calls `network.plan_isolation`:
   - `SEGMENT_OF_PIPE` (`algo.WCC` over `Junction`, `Pipe` and `Seized` nodes) gives segment members `{P-107, J-12, P-136}`.
   - `BOUNDARY_VALVES` gives the valves to close: `V-212, V-213, V-264, V-265`.
4. **Validation on a sandbox.** `network.simulate`:
   - `GRAPH.COPY riverbend → riverbend__sim_<id>` (retried on fork contention).
   - `SIM_DELETE_CLOSED`, then `SIM_DRY_PIPES` measures the baseline (outages already caused by other work orders).
   - `SIM_DELETE_VALVES` applies the plan's closures, then `SIM_DRY_PIPES` again. Components with no `Source` are dry.
   - `SIM_SUPPLY_PATH` (`algo.SPpaths`) proves each critical customer is still supplied, or shows the path still feeding the break.
   - The sandbox is deleted.
5. **Contingencies.** For each valve to close, `_contingency` merges the neighbouring segment (`VALVE_NEIGHBOURS_ONE`, `SEGMENT_OF_NODE`), recomputes the boundary and simulates again.
6. **Result.** The plan, warnings, contingencies and history (`PIPE_HISTORY`) return as JSON. Every query run is recorded by `db.run()`'s tracer (name, Cypher, params, rows, ms, graph) and shown in the UI's evidence panel. The reply text is built only from tool results (`agent.summarize`).
7. **Action (human).** The *Approve* button calls `POST /api/approve` with an optional acknowledgement list, then `agent.approve` → tool `issue_work_order` (`confirmed_by_human=True`, `expected_*` from the plan shown) → `network.issue_work_order`, which:
   - de-duplicates valves and refuses unknown or seized ones;
   - re-simulates, and refuses if the break isn't isolated, if the impact differs from what was shown (`stale`), or if critical customers aren't acknowledged by exact ID (`needs_acknowledgement`);
   - writes `CREATE_WORK_ORDER`, `WORK_ORDER_CLOSE_VALVES` (status `closed`, `:Open` removed) and `WORK_ORDER_NOTIFY`.
8. **Restore (human).** `POST /api/restore` → `network.restore_work_order`, which:
   - refuses work orders that are not `issued`;
   - re-opens each valve on a sandbox first, and keeps it closed if another issued work order holds it or if re-opening it would re-feed another active break (`TRANSFER_HOLD` hands it over);
   - then runs `REOPEN_VALVES` and `CLOSE_OUT_WORK_ORDER`.

**LLM mode** (only with `ANTHROPIC_API_KEY`): `agent._llm_turn` gives Claude (`claude-opus-5-5` by default) the 7 non-gated tools. Each tool result goes back to the model. The final reply is checked by `check_reply`: an ID or account count not present in the tool results gets the reply replaced with a summary built from the results. Any API exception falls back to the offline parser with a visible note.

---

## 3. Graph schema, algorithms and queries

**Nodes (seeded counts):** `Source` 2, `Junction` 32, `Pipe` 53, `Valve` 96, `Customer` 56, `Incident` 3 (synthetic history); 242 in total. `WorkOrder` nodes are created at runtime.

**Relationships (seeded counts):**
- `CONNECTS` (202): Junction/Source ↔ Valve ↔ Pipe, or Junction ↔ Pipe where a leg has no valve. The stored direction is arbitrary; every query treats it as undirected.
- `SERVED_BY` (56): Customer → Pipe.
- `ON` (3): Incident → Pipe.
- At runtime: `WorkOrder-[:FOR]->Incident`, `WorkOrder-[:CLOSES {seq | inherited_from}]->Valve`, `WorkOrder-[:NOTIFIES {priority, channel:'simulated'}]->Customer`.

**Why pipes and valves are nodes:** a valve node between a junction and a pipe means "close the valve" is "remove that node from the connectivity computation". A pipe node lets breaks, customers and incidents attach to it.

**State labels drive the algorithms:**
- `:Open`: valve passes water (supply analysis).
- `:Seized`: valve can't be closed (isolation analysis treats it as pass-through).
- Properties: `status` (open/closed), `condition` (ok/unknown/seized), `last_exercised`.

**Critical customers:** `C-HOSP` (hospital, P-119), `C-CARE` (care home, P-116), `C-MED7` (medical-priority account, P-151 Willow Ct). The school `C-SCHOOL` is flagged as sensitive, not critical.

**Algorithms and graph features used:**

| Feature | Where | Purpose |
|---|---|---|
| `algo.WCC` with `nodeLabels` filter | `SEGMENT_OF_PIPE`, `SEGMENT_OF_NODE`, `ALL_SEGMENTS` (live) | Isolation segments; seized valves included as pass-through |
| `algo.WCC` on the sandbox | `SIM_DRY_PIPES` (closed valves deleted), `SIM_DRY_PIPES_BY_LABEL` (`:Open` toggled) | Who loses water; break isolated?; ranking; restore check |
| `algo.SPpaths` (`relDirection:'both'`) | `SIM_SUPPLY_PATH` | Proof that a customer is supplied, or the path still feeding the break |
| `GRAPH.COPY` | `network.sandbox()` / `_copy_with_retry` | What-if simulation without touching the live graph |
| Label `SET` / `REMOVE` | `MARK_SEIZED`, `WORK_ORDER_CLOSE_VALVES`, `REOPEN_VALVES` | State changes that every later query sees |
| `ro_query` | every non-write call in `db.run` | Read paths cannot write (tested) |

Every product query is one of 39 named constants in [`shutoff/queries.py`](shutoff/queries.py). The only exception is a one-line node count in `api.py`'s `/api/health`. The key ones are reproduced verbatim in [`README.md`](README.md) under "Cypher and algorithms the product depends on". The model never writes Cypher, and tool arguments are format-validated (`tools.py`) and resolved against the graph before use.

**Measured latency** (local Docker on the build laptop, median of 5, 17 Oct 2026): segment WCC 1.5 ms; one simulation (copy, two WCC passes, paths, delete) 98 ms; full plan with 4 contingencies 503 ms; valve ranking 443 ms.

---

## 4. What is verified, mocked/simulated, or untested

| Capability | Status | Evidence |
|---|---|---|
| WCC segments correct | **Verified** | `test_falkordb_wcc_segments_match_independent_union_find` (all 53 pipes vs pure-Python union-find) |
| Plan A / Plan B / naive-plan outcomes | **Verified** | `test_plan_a_*`, `test_seized_valve_forces_plan_b_*`, `test_naive_two_valve_plan_*` (every leak-path hop checked against real links) |
| Sandbox never changes the live graph | **Verified** | `test_simulation_never_modifies_the_live_graph` |
| `GRAPH.COPY` under background-save contention | **Verified** | `test_sandbox_survives_a_concurrent_background_save` (forced BGSAVE ×25) |
| Work order write and restore; restore safety | **Verified** | `test_issuing_a_work_order_*`, `test_restore_*` (3 tests) |
| Approval safeguards (seized, leaking, stale impact, critical acknowledgement, human confirmation, de-duplication) | **Verified** | `test_work_order_refuses_*`, `test_approval_is_bound_*`, `test_stale_proposal_*`, `test_critical_cutoff_*`, `test_typed_approval_*`, `test_issue_work_order_requires_human_confirmation`, `test_duplicate_valve_ids_*` |
| Impact attribution with other work orders active | **Verified** | `test_plan_impact_excludes_outages_from_other_work_orders` |
| Unknown and ambiguous input not guessed | **Verified** | `test_unknown_and_ambiguous_locations_are_not_guessed`, `test_location_normalisation` |
| Offline agent conversation | **Verified** | `test_offline_agent_conversation_end_to_end`, `test_offline_parser_only_records_valves_described_as_seized` |
| HTTP API | **Verified** | `test_http_api_end_to_end` (FastAPI TestClient) |
| MCP server | **Verified** | in-process (`test_mcp_server_*`); also smoke-tested once over a real stdio client during the audit (9 tools, correct results). The stdio check is not part of the test suite |
| Clean install from scratch | **Verified once** | During the audit (17 Oct): new venv + `requirements.txt` + new container from the pinned digest, then seed, 26/26 tests (pre-fix suite), API answered. Not repeated for the 40-test suite |
| Claude tool loop | **Mocked only** | `test_llm_loop_*` use a fake client. The real request shape (`client.beta.messages.create` with `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`, `output_config={"effort": "medium"}`) has **never been sent to the live API** |
| Crew dispatch, customer notices | **Simulated** | Recorded as `NOTIFIES {channel:'simulated'}` edges; nothing is sent. Labelled in UI, API and docs |
| Hydraulics (pressure, flow) | **Not modelled** | Connectivity only |
| UI rendering | **Manually checked** | Screenshots in `docs/screenshots/`; no automated UI tests |
| Non-isolating contingency display | **Untested** | No Riverbend valve triggers it |
| Real utility networks / scale | **Untested** | 53-pipe synthetic grid only |

---

## 5. Main risks and limitations

1. **Synthetic, connectivity-only network.** Results on real networks (thousands of pipes, pressure zones, check valves, PRVs) are untested. Valves are modelled as two-way isolation points, and pressure adequacy isn't computed.
2. **Live Claude API mode is unverified.** The default demo uses the deterministic offline parser, which understands a limited set of phrasings.
3. **GRAPH.COPY cost grows with graph size.** It is fine at 242 nodes (~100 ms per simulation). On a large network, contingency and ranking loops would need a different strategy, such as label-toggling on one sandbox, which the ranking already uses.
4. **Fork dependence.** `GRAPH.COPY` forks the server; contention with Redis background saves is handled by a bounded retry (≤ 3 s), not eliminated.
5. **Conservative seized handling.** A valve recorded as seized is pass-through even if it is currently closed, which can over-size a segment.
6. **The valve-exercise score is a heuristic:** (accounts + 60 per critical customer) × condition factor × age factor. It is not calibrated.
7. **In-memory sessions;** one demo user at a time. Work-order IDs come from a count, which isn't concurrency-safe.
8. **Process risk:** the code was written by an AI coding assistant (see `docs/AI_DISCLOSURE.md`; the participant's section is not yet filled in). There is no public repo yet, and git commits carry the build machine's global author identity.

---

## 6. How to run, test and demo

**Requirements:** Docker (tested with Docker 29.5.3; FalkorDB graph module 4.22.1 via the pinned image digest in `docker-compose.yml`), Python 3.12 (tested with 3.12.0), ports 6379, 3000 and 8000 free. No API key is needed.

Start FalkorDB:
```bash
docker compose up -d
```
Create the environment and install dependencies:
```bash
python -m venv .venv
```
```bash
.venv/Scripts/python -m pip install -r requirements.txt
```
Load the synthetic network (the API also seeds it automatically if the graph is missing):
```bash
.venv/Scripts/python -m shutoff.seed
```
Run the app, then open http://localhost:8000:
```bash
.venv/Scripts/python -m uvicorn shutoff.api:app --port 8000
```
Run the tests. They need the FalkorDB container and use their own `riverbend_test` graph:
```bash
.venv/Scripts/python -m pytest -q
```
Run the MCP server over stdio:
```bash
.venv/Scripts/python -m shutoff.mcp_server
```
On macOS or Linux, replace `.venv/Scripts/python` with `.venv/bin/python`.

Optional: set `ANTHROPIC_API_KEY` for Claude mode (unverified). `SHUTOFF_OFFLINE=1` forces offline mode. `FALKORDB_HOST`, `FALKORDB_PORT` and `SHUTOFF_GRAPH` override the defaults.

**Demo steps** (the buttons under the chat; click *Reset demo* first):
1. *Report break on Maple St*: 4 valves, 34 accounts, no critical customers, warning that V-265 is of unknown condition (2011), and the contingency predicting a dialysis cut-off if V-265 fails.
2. *Check crew's 2-valve plan*: rejected; the purple 20-hop leak path is drawn from the reservoir.
3. *Hospital supplied?*: cyan 14-hop supply path from Hilltop Tank.
4. *V-265 is seized*: re-plan with 7 valves and 66 accounts; Willow Ct and the medical-priority account cut off (unintended).
5. Tick the acknowledgement, then *Approve & issue work order*: WO-1001 is written, valves turn black, a priority notice is recorded (simulated).
6. *Unknown street* returns "Not in the graph". "Leak at Oak & 3rd" returns ambiguous with two candidate mains.
7. *Valves to exercise*: ranking from 96 simulated seized-valve scenarios (fewer once valves are closed or seized).
8. *Restore* (work-order bar): valves re-opened after the sandbox check.

The recorded walkthrough is `demo/shutoff-demo.webm` (~1:45, Playwright against the live app, captions overlaid by `demo/record_demo.py`).

---

## 7. Files to review

| Priority | File | What it is |
|---|---|---|
| 1 | `shutoff/queries.py` | Every Cypher query (39 named constants) |
| 1 | `shutoff/network.py` | Segment, simulation, contingencies, work order, safe restore, ranking |
| 1 | `tests/test_graph.py` | 31 integration tests against FalkorDB, including the union-find cross-check |
| 2 | `shutoff/tools.py` | Tool catalogue, argument validation, human-gated tools |
| 2 | `shutoff/agent.py` | Offline parser, approval interception, Claude loop, `check_reply` |
| 2 | `shutoff/town.py` | Synthetic network definition (missing valves, unknown-condition valves, customers) |
| 2 | `AUDIT.md` | Forensic audit: findings, fixes, evidence |
| 3 | `shutoff/db.py` | Connection and query tracer (`ro_query` for reads) |
| 3 | `shutoff/seed.py` | Loader, indexes |
| 3 | `shutoff/api.py` | HTTP endpoints, start-up (seed, stale sandbox cleanup) |
| 3 | `shutoff/mcp_server.py` | MCP server (9 tools, destructive annotations) |
| 3 | `web/app.js`, `web/index.html`, `web/style.css` | UI: map, plan panel, acknowledgement, evidence panel |
| 3 | `tests/test_unit.py`, `tests/conftest.py` | 9 DB-free tests; test-graph fixture |
| 4 | `README.md`, `docs/EXPLAINER.md`, `OWNER_BRIEFING.md` | Product docs and plain-English explanation |
| 4 | `docs/AI_DISCLOSURE.md`, `docs/SUBMISSION_CHECKLIST.md` | Disclosure (participant section pending), checklist |
| 4 | `docker-compose.yml`, `requirements.txt` | Pinned FalkorDB image digest; pinned Python dependencies |
| 4 | `demo/record_demo.py`, `demo/shoot.py`, `docs/screenshots/` | Demo capture scripts and screenshots |
