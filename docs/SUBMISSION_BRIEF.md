# Submission brief (copy-ready text for the WeMakeDevs form)

The submission form sits behind the WeMakeDevs sign-in, so its exact fields could not be inspected. The text below
covers the fields hackathon forms usually ask for. Paste only the parts the form asks for. Every claim below is
backed by the repository: the tests, the benchmark, or the video.

**Project title:** ShutOff: water-main-break isolation on FalkorDB

**One-liner:** An agent that turns a broken-main report into a tested valve-closure plan. FalkorDB derives the
shutdown area, simulates the closures on a graph copy, shows the evidence path when a plan fails, and re-plans when a
crew finds a seized valve. It writes a (simulated) work order only after explicit human approval.

**Track:** Track 1, Agents That Act on Connected Data.

**Why Track 1:** the agent acts on connected data rather than just answering questions about it:
- It plans an intervention, tests it against the graph, and refuses plans that fail their post-conditions.
- It records crew observations as graph state (`:Seized`) and re-plans from them.
- It writes `WorkOrder`/`CLOSES`/`NOTIFIES` records only when a human approves the exact plan revision and impact.
- Its tools are also served over MCP, and the destructive tools require human confirmation.

**Problem:** when a water main breaks, a supervisor has to decide quickly which valves to close.
- The obvious "close the two nearest valves" plan often fails, because networks are looped.
- A valve that has seized silently enlarges the shutdown.
- Closing a segment can cut off customers *outside* it, such as a home-dialysis patient at the end of a cul-de-sac.

These are connectivity questions on a looped graph.

**Insight:** each of those three risks is a graph operation:
- the isolation segment is a connected component, with operable valves acting as walls;
- a seized valve is a label change that merges components;
- an unintended outage is a component with no source after the closures.

**What FalkorDB contributes:**
- `algo.WCC` derives the segments.
- `GRAPH.COPY` provides sandboxes to simulate each plan and each "what if this valve won't close" contingency.
- `algo.SPpaths` provides the evidence paths: the route water still takes to the break, and each hospital's surviving
  source path.
- State labels (`:Open`, `:Seized`) record observations.
- Graph writes record approved work orders.

Without the graph queries there is no plan, impact, evidence, contingency or restore check. When the optional
language model is enabled, it only chooses among fixed tools and never writes Cypher.

**Highlights:**
- The full incident runs in the real UI: break report, then the naive 2-valve plan is rejected with the leak path drawn.
  A seized valve is reported, the plan is revised to 4 valves, the critical customer is acknowledged, and the work order
  is approved (simulated).
- Approval is bound to the plan revision, the predicted impact and the affected-customer set. Questions, what-ifs and
  negations never write.
- Segmentation was cross-checked against US EPA WNTR `valve_segments` on the public EPANET networks Net1, Net3 and ky4
  (964 nodes, 1,158 links). All 12 configurations and all 60 seized-valve trials matched (`docs/BENCHMARK_WNTR.md`).
- 51 core tests run against a real FalkorDB instance (54 with the optional WNTR benchmark), including a real MCP stdio
  session.
- A full plan with 4 contingency simulations takes about 0.5 s locally.

**Repository:** https://github.com/Kartikinfinity/shutoff-graph-agent

**Demo video (3:01):** `demo/shutoff-demo.mp4` in the repository:
https://github.com/Kartikinfinity/shutoff-graph-agent/blob/main/demo/shutoff-demo.mp4
(Replace this with a YouTube or Drive link if you upload it there.)

**Deployment:** there is no hosted deployment. It runs locally with one command:
`git clone …`, then `docker compose up -d`, then open http://localhost:8080. No API key is needed.

**Limitations (stated honestly):**
- Connectivity only: there is no pressure or flow modelling.
- Synthetic town: segmentation is validated on public EPANET topologies with generated valve locations.
- Work orders and notices are graph records only; nothing is dispatched or sent.
- One local operator, with no authentication.
- The deterministic parser covers a limited set of phrasings, and Claude mode has not been tested live.
- ShutOff does not control any real infrastructure.

**AI disclosure:** see `docs/AI_DISCLOSURE.md`. The participant section must be filled in by the participant.

## Remaining actions for the participant
1. Watch `demo/shutoff-demo.mp4` once, start to finish, with sound. Optionally upload it to YouTube (unlisted) or
   Drive, if the form wants a video link.
2. Fill in the "What the participant did" section of `docs/AI_DISCLOSURE.md` honestly, then commit and push it.
3. Sign in at WeMakeDevs, open the Graph Hacks submission, choose Track 1, and paste the text above plus the repo and
   video URLs.
4. Confirm the team members on the submission, and submit before **18 Oct 2026, 11:59 PM IST**.
