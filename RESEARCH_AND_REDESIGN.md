# ShutOff: research findings and redesign decisions (18 Oct 2026)

Inputs:
- An independent review of commit `54e5e78` (a GPT-generated audit supplied by the team; not included in this repository).
- The actual code at commit `f588ce2`.
- About 45 minutes of targeted research in official and primary sources.

Each research item ends with the decision it changed. All links were opened on 18 Oct 2026 unless noted.

## 1. Official requirements (re-checked today)

The [rules page](https://www.wemakedevs.org/hackathons/falkordb/rules) is unchanged:
- FalkorDB must be primary and central (Rules 2–3).
- Only public, synthetic or own data (Rule 6).
- Main work starts after 15 Oct (Rule 8).
- Submission: a public repo, a README with setup and graph model, the Cypher or algorithms, a demo video, deployment or local setup, and the track (Rule 10).
- AI use disclosed (Rule 11); participants can explain the work (Rule 12); AI-only projects may be rejected (Rule 13).

**No judging criteria are published.** The only judging statement we know of is in the [official guide](https://www.wemakedevs.org/blogs/how-to-build-an-agent-that-reasons-over-a-graph): judging "comes back to whether the graph is doing work the agent could not do without it". The same guide shows Blast Radius writing `Finding` memory nodes, so it is not read-only. Our docs now say this correctly.

## 2. Real water-distribution modelling and operations

| Source | Finding | Decision |
|---|---|---|
| [US EPA, EPANET](https://www.epa.gov/water-research/epanet) | EPANET simulates hydraulics: pressures, flows, tanks, pumps, and valve types ("shutoff, check, pressure regulating, and flow control"). It is public domain; the current release is 2.2. | ShutOff stays connectivity-only and says so in the UI, README and evidence ("source path, not pressure"). Adding a hydraulic solver is out of scope for the deadline. |
| [WNTR `generate_valve_layer`](https://github.com/USEPA/WNTR/blob/main/wntr/network/layer.py) and [`valve_segments`](https://github.com/USEPA/WNTR/blob/main/wntr/metrics/topographic.py) (WNTR 1.5.0, Revised BSD) | The EPA's resilience toolkit models isolation valves as (node, link) pairs and computes segments by removing valved links and labelling connected components. This is the same concept ShutOff implements in FalkorDB. | **Used as an independent oracle** (section 3). |
| [Jun et al., *Water Resources Management* 22(6), 2008](https://link.springer.com/article/10.1007/s11269-007-9188-6) | Segments, and unintended isolation when segments are closed, are established concepts. | Domain framing kept. Wording: ShutOff "derives and validates" an isolation plan; it is not claimed optimal. |
| [Esri utility network isolation trace](https://doc.esri.com/en/arcgis-pro/latest/help/data/utility-network/isolation-trace.html) | Commercial GIS isolation works from a starting point and barriers, and "a set of isolating features is returned as a result selection set." | The UI follows the same mental model: a break location, the isolating valves, the affected features, and selection linked to the map. |

## 3. Public benchmark data: evaluated, used for validation, not for the demo

- **What exists.** WNTR 1.5.0 ships the EPANET example networks Net1, Net2 and Net3, plus Kentucky networks (ky4, ky10). Net3 is [based on the North Marin Water District, Novato, CA](https://uky.edu/WDST/SystemPages/21System_Page_NMarinWD.html). EPANET files carry pipes, nodes, pumps and control valves, but **no isolation-valve locations and no street names**.
- **Option A: make Net3 the demo network.** Rejected. Valves would have to be generated anyway; there are no street names for location reports; and it would replace a scenario that is already tested and reproducible on the day of the deadline.
- **Option B: use it to validate the graph core.** Done: [`benchmarks/wntr_crosscheck.py`](benchmarks/wntr_crosscheck.py) loads a network into a separate FalkorDB graph with ShutOff's schema. It places valves with WNTR's own generator (N-1, N-2 and random layers) and compares ShutOff's segment query with `wntr.metrics.valve_segments`.
- **Result.** Identical partitions in **12/12 configurations** (Net1, Net3, ky4 with 964 nodes and 1,158 links) and **60/60 seized-valve trials**. WCC took ≤ 81 ms on ky4. Details are in [docs/BENCHMARK_WNTR.md](docs/BENCHMARK_WNTR.md); optional tests are in `tests/test_benchmark.py`.
- **Scope.** This validates segmentation on real topologies. It does not validate valve placement, hydraulics, or the rest of the workflow on those networks.

## 4. Mapping options

| Option | Evidence | Verdict |
|---|---|---|
| Google Maps Platform | [Pricing](https://mapsplatform.google.com/pricing/) lists free monthly calls per SKU (70K Essentials, 35K Pro, 7K Enterprise), with charges billed to the account after that. It needs an API key and a billing setup. | Rejected. Riverbend's coordinates are synthetic, so placing them on a real city map would suggest real infrastructure. It also adds keys and billing to a local demo. |
| Leaflet + OpenStreetMap tiles | The [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/) requires visible attribution, forbids heavy use, bulk prefetch and offline use, and offers "no SLA or guarantee". | Rejected for the same reason (no defensible geography). A demo that depends on someone else's best-effort tile server is also less reliable. |
| Leaflet with `CRS.Simple` | The [Leaflet tutorial](https://leafletjs.com/examples/crs-simple/crs-simple.html) supports non-geographic x/y maps. | Possible, but it adds a dependency for pan and zoom we can do in SVG. Not needed. |
| **Schematic SVG (chosen)** | No keys, costs or attribution, and it is honest about synthetic coordinates. | Kept and improved: fit-incident and fit-network controls, linked selection, keyboard access, and the label "Simulated network · schematic layout, not geographic coordinates". |

## 5. Free hosted LLM APIs

| Provider | What the official docs say | Implication |
|---|---|---|
| Groq | [Tool use](https://console.groq.com/docs/tool-use): "All models hosted on Groq support tool use." [Rate limits](https://console.groq.com/docs/rate-limits): free and developer plans exist; the page shows developer-plan base limits (e.g. gpt-oss-120b: 30 RPM, 1K RPD) and does not clearly state free-plan figures or card requirements. [Data](https://console.groq.com/docs/your-data): no retention of inference data by default, logs up to 30 days for abuse or troubleshooting, and zero data retention available. | Technically compatible. Limits and plan terms must be confirmed in the account. |
| Google Gemini API | [Function calling](https://ai.google.dev/gemini-api/docs/function-calling) is supported. [Pricing](https://ai.google.dev/gemini-api/docs/pricing) shows a free tier for current Flash models, with "Used to improve our products: Yes" on the free tier. The [terms](https://ai.google.dev/gemini-api/terms) say human reviewers may read free-tier inputs and outputs: do not submit sensitive data. [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) for the free tier are shown per project in AI Studio, not on the page. | Usable for synthetic data, but free-tier content is used for product improvement. |

**Decision: no new LLM provider.** No credentials are available on this machine, so no integration could be verified before the deadline. Adding an unverified second mode would widen the gap the reviewer flagged.
- ShutOff keeps its **deterministic planner as the default and the only verified mode**.
- The optional Claude mode stays labelled unverified.
- In every mode the model only picks fixed tools: it cannot write Cypher, close or re-open valves, or record a valve condition from a question (enforced server-side).

## 6. Review findings and what was done

| Review finding | Fix (commit) | Evidence |
|---|---|---|
| Critical 1: questions, what-ifs and negations triggered writes ("Do not restore WO-1001." restored it) | Mutations need an observation or an exact affirmative command. Questions use the read-only `valve_status` tool; what-ifs are answered from the plan's contingency simulation (`962028c`) | `test_questions_and_hypotheticals_never_record_a_valve_condition`, `test_negated_or_question_restore_text_does_not_restore`, `test_llm_mode_cannot_record_a_condition_from_a_question` |
| Critical 2: an old plan survived a failed new report | A new report clears the plan. Each plan gets a revision ID (`IR1-r2`); approval must name the revision on screen and is bound to the exact affected-customer set | `test_failed_new_report_drops_the_previous_plan`, `test_approval_is_bound_to_the_plan_revision_on_screen`, `test_approval_is_bound_to_the_exact_affected_customers`, HTTP test |
| High 1: MCP gating inconsistent | MCP `restore_service` requires `confirmed_by_human`; `issue_work_order` requires the shown impact. The trust boundary is documented: the client asserts confirmation and the server does not authenticate a person | `test_mcp_server_exposes_the_same_tools_and_gates_destructive_ones`, unit tests |
| High 2: reply check is lexical | Labelled precisely: "IDs and counts checked against graph results". Consequence wording comes from deterministic summaries | UI log tags, README |
| High 3: claims exceeded evidence | "Break isolated" became "In simulation the break would be disconnected". "Close in this order" became "Valves to close" (listed by ID, no sequence). "Supplied" became "source path (connectivity, not pressure)". Supply questions say which graph state they used | UI and `agent.summarize` |
| UX 1–5: approval lost its context; simulated looked real; Cypher-heavy; map not linked; small screens | Redesign (section 7) (`9630a21`) | Screenshots in `docs/screenshots/` |
| Optional 2: no real-network validation | WNTR cross-check (section 3) (`fcbc697`) | `docs/BENCHMARK_WNTR.md` |

Not done (deliberately): atomic multi-step writes and concurrency control, authenticated approvals, hosted deployment. ShutOff remains a local, single-operator demonstration, and the README says so.

## 7. UI redesign: operator workflow

The goal: answer five questions on one screen, in order.

**1. What happened and where?** An **incident strip** under the app bar shows:
- the state (`No incident`, `Location needed`, `Simulation ready`, `Needs acknowledgement`, `Work order recorded`, `Service restored`);
- the main and pipe ID, and the plan revision;
- the latest crew observation;
- three numbers (valves, accounts, critical cut-offs) with deltas when a re-plan changes them (+3 / +32 / +1).

**2. What is affected?** A **schematic map** fills the centre:
- amber = would be shut off (simulation), red dashed = would lose supply outside the area, grey dashed = out of service under a recorded work order;
- priority customers are labelled in words (Hospital, Care home, Dialysis account, School), not just letters.

**3. Which plan, and its trade-offs?** The **decision rail**, in order:
1. the simulation verdict;
2. a **"Risk to watch"** callout naming the riskiest valve and what happens if it fails;
3. priority customers (each row draws its proof path on the map);
4. valves to close (each row highlights its valve on the map);
5. who loses water, inside versus outside the area;
6. the crew's alternative, if one was checked;
7. hypothetical "if a valve cannot close" rows that **preview on the map with a pink dashed outline and record nothing**.

**4. What evidence?** The **graph evidence** section is collapsed by default. It states in plain words why these valves (WCC area, adjacent valves, sandbox WCC, SPpaths), with every query and its Cypher one level deeper.

**5. What must the supervisor approve?** An **approval dock** is pinned to the bottom of the rail. It repeats the main, plan revision, valves, accounts and named critical customers, holds the acknowledgement checkbox, and offers "Re-plan" if the network changed. After approval it shows the recorded work order and a two-step "Restore service".

Other design points:
- **Incident log** (left): structured entries (You, ShutOff, Crew observation saved, Supervisor action) with one-line headlines; full text sits under "Details". One primary demo button; other demo inputs are collapsed.
- **Motion** reveals a change and then stops: the proof path draws once (no animated flow, since there is no hydraulic solver), new valves ping twice after a re-plan, changed numbers flash once, view changes ease over 260 ms, and a slow break pulse plays. All of it is off under `prefers-reduced-motion`.
- **Themes:** light and dark, from one token set, following the system setting; the toggle is saved locally.
- **Responsive layouts:**
  - At 1440 px and wider: log, map and rail side by side.
  - Between 900 and 1439 px: the log becomes a drawer, leaving map plus rail.
  - Below 900 px: Plan / Map / Log tabs, with the approval dock pinned to the bottom of the screen.
- **Accessibility:** keyboard focus reaches the plan valves and priority customers, and their details appear on focus, not only on hover. Every status colour is paired with a word or shape.

## 8. Remaining limitations

- Synthetic network, connectivity only, valves modelled as two-way isolation points, no pressure zones or check valves.
- The deterministic parser understands a limited set of phrasings. Claude mode has not been run against the live API. (Later on 18 Oct the Groq `openai/gpt-oss-20b` mode was live-tested; see `docs/LIVE_LLM_TEST.md`.)
- The approval safeguards are logical checks for one local operator. There are no authenticated users, no atomic transactions and no concurrent-operator safety.
- `confirmed_by_human` on MCP is asserted by the client.
- The valve-exercise ranking is a transparent heuristic, not a calibrated risk model.
