# ShutOff — forensic audit (17 Oct 2026, ~19:30 IST)

Scope: the actual code, a running FalkorDB, real test runs and probes. Rated PASS / PARTIAL / FAIL /
UNVERIFIED. The pre-audit checkpoint is git commit `832a626`. The pre-audit video is kept as
`demo/shutoff-demo.v1-pre-audit.webm`.

## 0. What the official sources say (re-read 17 Oct 2026)

**Official requirements** (rules page, Rule 10 and others):
- Public repo.
- README with setup and the graph data model.
- The Cypher queries or graph algorithms the product depends on.
- A demo video.
- A live deployment *or* complete local setup instructions.
- The track named at submission.
- FalkorDB primary and central (Rules 2–3); public, synthetic or own data only (Rule 6); main work after 15 Oct (Rule 8); AI use disclosed (Rule 11); participants can explain the graph model, queries and architecture (Rule 12); fully AI-generated projects without meaningful participant contribution may be rejected (Rule 13).

**Track 1 focus** (event page): multi-hop reasoning; graph queries and algorithms as agent tools; tool selection through MCP; actions based on what the graph shows; explainable results. The prize is for "the best agent that uses a graph to make decisions and take action."

**Judging criteria:** **none are published** on the event, rules or schedule pages, and no demo video length is stated. The only judging statement found is in the official guide: "In all three tracks the judging comes back to whether the graph is doing work the agent could not do without it." Everything in §3 is my inference, not an official rubric.

**Blast Radius vs ShutOff.** Blast Radius answers "what depends on X / who to wake" by traversing an existing dependency graph. Its only write is a `Finding` memory node.

ShutOff's genuine differentiator: it **decides an intervention** (which valves to close), **tests it on a copy of the graph** (`GRAPH.COPY`), **checks post-conditions** (break dry, critical customers' supply paths), **changes the live graph** only after human approval, and **re-plans when the field contradicts the graph** (seized valve). The use case stands; nothing found argues against it.

## 1. Requirement and claim audit

Priority key: P0 = submission blocker or safety bug, P1 = materially wrong or misleading, P2 = should fix, P3 = note.

### Official requirements

| # | Requirement / claim | Status | Evidence | Judge impact | Correction (priority) |
|---|---|---|---|---|---|
| R1 | Public source repository | **FAIL** | Only a local git repo exists (`832a626`); no GitHub remote; `gh` CLI not installed | Hard requirement | You create a public GitHub repo and push (**P0, user**) |
| R2 | README with setup + graph model | PASS | `README.md` "Run it locally", "Graph model" | — | Re-check after fixes |
| R3 | Cypher / algorithms documented | PASS | README "Cypher and algorithms…", `shutoff/queries.py` | — | — |
| R4 | Demo video | MET (18 Oct) | `demo/shutoff-demo.mp4` (3:01, narrated, captions + `.srt`) recorded against the live app after the fixes and committed to the repo; objective checks in `demo/verify_video.py` | Required | Optional: also upload to YouTube/Drive for the form link (**user**) |
| R5 | Deployment or local setup | PARTIAL | Local setup only (rules allow this with a video) | Deployment is "preferred" | Optional; skip unless time remains (P3) |
| R6 | Track named in submission | UNVERIFIED | Submission form | Required | Select Track 1 in the form (**P0, user**) |
| R7 | FalkorDB primary and central | PASS | No other database. Segments (`SEGMENT_OF_PIPE`, `algo.WCC`), impact (`SIM_DRY_PIPES`), proof paths (`algo.SPpaths`), plan validation and ranking all run as graph queries (§2) | Core of judging per the official guide | — |
| R8 | Permitted data only | PASS | `shutoff/town.py` builds everything synthetically; the UI header and README say so | — | — |
| R9 | Main work after 15 Oct | PASS | All files created 17 Oct 2026 | — | — |
| R10 | AI-use disclosure | PARTIAL | `docs/AI_DISCLOSURE.md` exists; participant section is a placeholder | Required | Fill in honestly (**P0, user**) |
| R11 | Participant can explain; meaningful contribution (Rules 12–13) | UNVERIFIED (risk) | Code was written by the AI assistant; the participant chose the idea | Possible rejection under Rule 13 | Read `docs/EXPLAINER.md`, run the demo, record the narration in your own voice, make and own at least some changes (**P0, user**) |
| R12 | Track 1: multi-hop reasoning | PASS | 20-hop leak path, 14-hop supply proof, segment merging through seized valves | — | — |
| R13 | Track 1: graph algorithms as tools | PASS | `algo.WCC` in 6 queries, `algo.SPpaths` in proof paths, `GRAPH.COPY` sandboxes | — | — |
| R14 | Track 1: tool selection through MCP | PARTIAL | MCP server verified over a **real stdio client** (9 tools; `locate_break` and `check_closure_plan` returned correct results). The web agent calls `tools.py` directly, and LLM tool selection is untested live | Judges may want to see MCP in use | Show MCP in the demo or README; live LLM test needs a key (P2) |
| R15 | Track 1: actions based on the graph | PARTIAL → fixed in §4 | Work orders and seized-valve reports write to the live graph; safety bugs T6–T9 | Actions are the track's point | See T6–T9 |
| R16 | Track 1: explainable results | PASS | Evidence panel lists every tool call's Cypher, params, rows and latency; paths drawn on the map | — | — |

### Technical claims (verified by test or probe)

| # | Claim | Status | Evidence | Impact | Correction (priority) |
|---|---|---|---|---|---|
| T1 | WCC segments are correct | PASS | `test_falkordb_wcc_segments_match_independent_union_find` compares all 53 pipes with a pure-Python union-find | Credibility | — |
| T2 | Seized valves extend the segment | PASS | `test_seized_valve_forces_plan_b...`: 7 valves, 66 accounts, C-MED7 unintended | — | A valve seized while *closed* is still treated as pass-through (conservative). Document (P3) |
| T3 | Simulation never changes the live graph | PASS | `test_simulation_never_modifies_the_live_graph` (96 `:Open` valves before/after, no leftover sandboxes) | Trust | — |
| T4 | Leak/supply paths are real pipe connections | PASS | Test checks every hop of the leak path against the town's links | — | — |
| T5 | Impact numbers when another work order is active | **FAIL** | Probe: with WO-1 active, a plan for P-119 reported 49 accounts out, listing WO-1's 34 Maple St accounts as its own "unintended" cut-off. Real new impact: 15 | Wrong numbers to a supervisor | Baseline the dry set before applying the planned closures; report "already out" separately (**P1**) |
| T6 | Restoring a work order is safe | **FAIL** | Probe: WO-2 also listed V-213 (held closed by WO-1). Restoring WO-2 re-opened V-213, and **P-107, still broken under WO-1, was re-fed** | Safety bug in the core action | Restore re-opens only valves no other active work order holds, and re-verifies on a sandbox that no other active break is re-fed; valves still needed pass to the dependent work order (**P0**) |
| T7 | Restore is idempotent | **FAIL** | Probe: restoring the same WO twice returned "restored" both times and re-ran the valve opening | Can re-open valves another WO closed later | Only `status:'issued'` work orders can be restored (**P1**) |
| T8 | Approval commits what the human saw | **FAIL** | `issue_work_order` re-simulates but commits even if the impact changed since the plan was shown (e.g. another WO closed valves in between) | A different outage than the one approved | Approval carries the expected accounts and critical customers; the server refuses if the re-simulation differs (**P1**) |
| T9 | Dialysis / critical account handling | PARTIAL | Detected, flagged CRITICAL, predicted in advance by the contingency table, priority notice recorded (simulated). But approval needs **no explicit acknowledgement** that a critical customer loses water, and the docs say "protected", which overstates it | Safety story judges will probe | Server-side requirement to acknowledge the exact critical customers (UI checkbox, MCP param, typed "approve and acknowledge"); reword "protection" to "flagged, acknowledged, priority notice" (**P1**) |
| T10 | The model can't close valves | PASS | `model_tools()` excludes the human-gated tools (`test_model_is_not_offered_human_gated_tools`); MCP requires `confirmed_by_human` (`test_mcp_server...`) | — | — |
| T11 | Read paths can't write | PASS | `test_read_queries_cannot_write` (`ro_query`) | — | — |
| T12 | Offline agent end to end | PARTIAL | `test_offline_agent_conversation_end_to_end` passes, **but** the probe "Water main broken near V-230 on Pine St" **recorded V-230 as seized** (keyword "broken" anywhere plus any valve ID) | A false field report changes every later plan | Require the seized wording to sit next to the valve ID; drop "broken" (**P1**) |
| T13 | Claude API mode | UNVERIFIED | No `ANTHROPIC_API_KEY` or `ant` login on this machine. Mocked-client tests pass (`test_llm_loop_*`). The live request shape (`betas` + `fallbacks="default"`) was never sent to the API. Any API failure falls back to offline mode with a visible note | Must not be claimed as working | Keep it labelled unverified; test live only if you provide a key (P2, user) |
| T14 | Replies can't invent IDs or results | PARTIAL | IDs are checked against tool results (`test_reply_check_*`). The count check accepts any number appearing anywhere in the evidence JSON, so it is too permissive | Over-claims grounding | Accept only numbers from numeric result fields, derived sums and "(N homes)" names (P2) |
| T15 | Duplicate valve IDs | FAIL (minor) | Probe: 5 IDs with one duplicate created 5 `CLOSES` edges | Messy work order | De-duplicate (P2) |
| T16 | Contingency rows that can't isolate | PARTIAL | `isolated` is computed but the UI table and summary ignore it | Could show a non-isolating fallback as usable | Show "cannot isolate" (P2) |
| T17 | MCP server | PASS | Real stdio client: 9 tools listed, calls return graph results; destructive tools annotated and gated | — | — |
| T18 | Test suite | PASS | 26/26 rerun at audit time | — | Add API-layer tests (P2): `api.py` had none |
| T19 | Clean start | see §1a | Fresh venv + `requirements.txt` + fresh FalkorDB container from the pinned image | Reproducibility | — |
| T20 | Demo video matches the app | PASS (pre-fix) | Recorded after the last UI change, against the live app; captions are overlays | Will be stale after T8/T9 | Re-record (P1) |
| T21 | Docs accurate | PARTIAL | README says "88 scenarios": that was with 8 valves closed or seized; a fresh network evaluates 96. "Protection" of the dialysis account overstated | Small credibility hits | Fix wording (P2) |
| T22 | Crash leaves sandbox graphs | PARTIAL | Sandboxes are deleted in `finally`, but a killed process can leave `riverbend__sim_*` graphs | Clutter only | Clean up on API start-up (P3) |
| T23 | Hydraulics | N/A (limitation) | Connectivity only; documented | Judges may ask | Keep stating it plainly |
| T24 | Edge direction | PASS (by design) | `CONNECTS` direction is arbitrary; every query is undirected (`-[:CONNECTS]-`, weak WCC, `relDirection:'both'`). Assumes no check valves or pressure zones | — | Document the assumption (P3) |

### 1a. Clean-start result

**PASS** (17 Oct 2026, ~19:35 IST). The run used a brand-new Python 3.12 venv and
`pip install -r requirements.txt` (exit 0), plus a new FalkorDB container started from the pinned image
digest on a spare port.
- `python -m shutoff.seed` loaded 242 nodes.
- `pytest` passed 26 of 26.
- `uvicorn shutoff.api:app` started; `/api/health` reported `falkordb: true, mode: offline`.
- A `/api/chat` break report returned the 4-valve plan.

## 2. Is FalkorDB doing the work? (centrality)

| Capability | Who computes it | Remove FalkorDB → |
|---|---|---|
| Isolation segment | `algo.WCC` over `Junction`/`Pipe`/`Seized` (live graph) | No plan |
| Valves to close | Cypher adjacency `BOUNDARY_VALVES` | No plan |
| Seized-valve re-plan | `SET v:Seized`, which changes the WCC input labels | No re-plan |
| Who loses water / break still fed? | `GRAPH.COPY` sandbox, `DETACH DELETE` closed valves, WCC with no `Source` | No impact analysis or validation |
| Proof paths (leak, hospital supply) | `algo.SPpaths` on the sandbox | No evidence |
| Contingencies | WCC of the neighbouring segment + boundary + sandbox simulation; Python only unions ID sets | No backup plans |
| Valve-exercise ranking | One sandbox, `REMOVE/SET :Open` per valve, WCC | No ranking |
| Memory (seized valves, incidents, work orders) | Graph nodes, labels and edges read by every later query | No persistence |

Python does name normalisation, set unions, scoring arithmetic and text summaries. The language model
(when enabled) only picks tools. **Verdict: PASS.** Every decision-bearing result comes from a graph
query, and the WCC result is independently cross-checked in tests.

## 3. Judge's-eye view (hypotheses, not an official rubric)

**Strongest reasons a judge might value it**
1. It matches the guide's stated test, "the graph is doing work the agent could not do without it": isolation, impact, validation and proof are graph algorithms, cross-checked against an independent implementation.
2. It *acts*: graph-state changes behind simulation-before-commit and human approval, and it re-plans when reality contradicts the plan (the seized valve). That is closer to "decide and take action" than read-only Q&A.
3. A concrete, uncommon domain grounded in engineering literature, with a demo moment that is honest and memorable: it predicts the dialysis cut-off *before* the valve fails.

**Strongest reasons it might lose**
1. Small synthetic grid (53 pipes) and connectivity-only analysis. A judge may see it as a toy next to entries using real public data.
2. The default "agent" is a deterministic keyword parser and the Claude mode is unverified live. Entries with a live LLM choosing tools over MCP may look more agentic.
3. Submission-process risk: no public repo or uploaded video yet, a mostly AI-built codebase (Rule 13), no live deployment, and a domain the judges may need explained quickly.

**Readiness before fixes:** functionally strong and graph-central, but **not submittable** (R1, R4, R10, R11) and with one safety bug in the core action (T6) plus misleading impact numbers (T5).

## 4. Fixes applied (17 Oct 2026, after checkpoint `832a626`)

| Finding | Fix (files) | Evidence |
|---|---|---|
| **T6** restore re-fed another active break (P0) | `restore_work_order` re-opens each valve on a sandbox first (`SIM_SET_OPEN` + WCC). Valves held by another issued WO, or whose re-opening would make another active break wet, stay closed; the dependent WO inherits them via `CLOSES {inherited_from}` (`network.py`, `queries.py`: `WORK_ORDER_BY_ID`, `REOPEN_VALVES`, `TRANSFER_HOLD`, `CLOSE_OUT_WORK_ORDER`) | `test_restore_never_reopens_a_valve_another_active_work_order_holds`, `test_restore_keeps_valves_another_break_relies_on_and_hands_them_over` |
| **New** (found while testing): `GRAPH.COPY failed, could not fork` (P0 reliability) | FalkorDB logs showed Redis's automatic BGSAVE forking at the same moment. `sandbox()` now retries the copy on fork contention (≤ 3 s) | `test_sandbox_survives_a_concurrent_background_save`: 25 forced BGSAVEs, 257 "Can't fork" collisions logged by FalkorDB, every simulation succeeded |
| **T7** restore repeatable (P1) | Only `status:'issued'` WOs can be restored; otherwise `refused` | `test_restore_is_not_repeatable` |
| **T5** impact double-counted other WOs (P1) | `simulate` applies existing closures first, measures a baseline dry set, then the planned closures; reports `already_out_accounts` separately (`SIM_DELETE_CLOSED`, `SIM_DELETE_VALVES`). UI shows an amber note | `test_plan_impact_excludes_outages_from_other_work_orders` (P-119 impact identical with and without WO-1; 34 accounts reported as already out) |
| **T8** approval not bound to the shown impact (P1) | `issue_work_order(expected=...)` refuses with `stale` if accounts out or critical IDs differ from the plan shown; UI, typed approval and MCP all pass it | `test_approval_is_bound_to_the_impact_that_was_shown`, `test_stale_proposal_is_refused_after_the_network_changes` |
| **T9** critical cut-off needed no acknowledgement (P1) | Server returns `needs_acknowledgement` unless `acknowledge_critical` equals the exact critical customer IDs; UI checkbox naming the customer; typed "approve and acknowledge"; MCP parameter. Docs reworded from "protected" to predicted, flagged, acknowledged, priority notice | `test_critical_cutoff_needs_exact_acknowledgement`, `test_typed_approval_needs_explicit_acknowledgement_for_critical_plans`, updated `test_offline_agent_conversation_end_to_end` |
| **T12** parser recorded valves from loose keywords (P1) | `_valves_described()`: the seized/ok wording must apply to that valve ("V-265 won't turn", "V-212 and V-213 are stuck", "stuck valve V-265"); "broken" removed | `test_offline_parser_only_records_valves_described_as_seized` ("broken near V-230" no longer writes; "V-230 is leaking and V-231 is seized" records only V-231) |
| **T14** count check too permissive (P2) | Accepts only numeric result values, list totals, and "(N homes)" from names | `test_reply_check_ignores_digits_inside_timestamps_and_ids`, `test_reply_check_accepts_totals_and_counts_from_customer_names` |
| **T15** duplicate valve IDs (P2) | De-duplicated in `issue_work_order` | `test_duplicate_valve_ids_are_deduplicated` |
| **T16** non-isolating contingency shown as usable (P2) | UI row and summary say "no backup isolation with operable valves" | Code path in `web/app.js`, `agent.summarize` (no Riverbend valve triggers it; not covered by an automated test) |
| **T18** no API tests (P2) | `test_http_api_end_to_end` drives health, network, chat, approve (with and without acknowledgement) and restore through FastAPI | Test passes |
| **T21** doc inaccuracies (P2) | README: 96 ranking scenarios on a fresh network, 40 tests, acknowledgement and safe restore described; EXPLAINER: precise wording about the dialysis account | — |
| **T22** stale sandboxes (P3) | API start-up deletes leftover `riverbend__sim_*` graphs; start-up moved to FastAPI `lifespan` (removes deprecation warning) | `test_http_api_end_to_end` starts the app |

**Test evidence:** `python -m pytest -q` reports **40 passed** (was 26), against a real FalkorDB.

**Not changed on purpose:** the UI design, the network data, the LLM prompt, and the scope (no new features).

**Still unverified:** the Claude API mode against the live API (no credentials on this machine).


## 5. Independent review follow-up (18 Oct 2026)

An independent review of `54e5e78` (GPT; `output/`, not committed) reproduced new defects with mocked calls. I
re-ran each probe against FalkorDB before fixing:

| Probe | Before | After (test) |
|---|---|---|
| "What if V-265 won't turn?" / "Is V-265 seized?" | Recorded V-265 as seized and re-planned | No write; answered from the contingency or `valve_status` (`test_questions_and_hypotheticals_never_record_a_valve_condition`) |
| "Do not restore WO-1001." | **Restored the work order** | No change; restore needs the button or an exact "restore WO-1001" (`test_negated_or_question_restore_text_does_not_restore`) |
| Valid plan → unknown Elm St report → "approve" | Issued the old P-107 plan | Plan cleared; nothing to approve (`test_failed_new_report_drops_the_previous_plan`) |
| Approving an older plan revision | Not checked | Refused (`test_approval_is_bound_to_the_plan_revision_on_screen`, HTTP test) |
| Same totals, different customers | Not checked | Refused (`test_approval_is_bound_to_the_exact_affected_customers`) |
| MCP restore without confirmation; issue without the shown impact | Allowed | Refused (`test_mcp_server_exposes_the_same_tools_and_gates_destructive_ones`, unit tests) |
| LLM records a condition from a question | Possible | Blocked server-side (`test_llm_mode_cannot_record_a_condition_from_a_question`) |

Wording and claims were corrected in the UI, the replies and the docs: simulated effects say "would"; "source path, not
pressure"; "valves to close" with no implied sequence; the Blast Radius comparison; observations persist. The UI was
redesigned and the WNTR cross-check added. Full details are in [RESEARCH_AND_REDESIGN.md](RESEARCH_AND_REDESIGN.md).

**Test evidence (18 Oct):** 51 core tests + 3 WNTR benchmark tests pass against FalkorDB, with 0 skipped in this
environment (WNTR installed).
