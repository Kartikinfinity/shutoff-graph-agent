# Submission brief (copy-ready text for the WeMakeDevs form)

**Form fields** (from the participant's screenshots of the signed-in form, 18 Oct):
- **Part 1:** project title*, description* ("What problem does your project solve, and who is it for?"), track*, live
  demo or deployment, "How did you use FalkorDB in your project?"*, blog link.
- The Part 1 counter shows 6 required fields but only 4 are visible, so two more required fields, probably the repo
  and video links, sit between "track" and "Live demo".
- **Part 2 (Feedback):** what you liked about FalkorDB*, what improvements you would like*. These are the
  participant's own opinions.

**Project title**
> ShutOff: water-main-break isolation agent on FalkorDB

**Description**
> When a water main breaks, a utility supervisor has minutes to decide which valves to close. The obvious plan,
> closing the two nearest valves, often fails because water networks are looped. A seized valve silently enlarges the
> shutdown. And closing a segment can cut off customers outside it, such as a home-dialysis patient at the end of a
> cul-de-sac.
>
> ShutOff is for water-utility supervisors and dispatchers. From a plain-language break report, it finds the broken
> main and works out the smallest area that valves can isolate. It simulates the closures on a copy of the network
> graph, then shows who loses water and which priority customers keep a supply path. It rejects a crew's two-valve
> plan by showing the exact route water would still take to the break, and it re-plans when a crew reports a seized
> valve. It records a work order only after a human approves that exact plan and acknowledges the critical customers.
>
> Limits: the data is fully synthetic, and the model covers connectivity only (no pressure or flow). Work orders and
> notices are simulated graph records. ShutOff does not control any real infrastructure.

**Track:** Agents That Act on Connected Data (Track 1) only.

**Repository:** https://github.com/Kartikinfinity/shutoff-graph-agent

**Demo video (3:01):** use a YouTube (unlisted) or Drive link. GitHub offers the 44 MB file only as a download:
https://github.com/Kartikinfinity/shutoff-graph-agent/raw/main/demo/shutoff-demo.mp4

**Live demo or deployment** (optional field). There is no hosted instance, so link the local setup instructions:
> https://github.com/Kartikinfinity/shutoff-graph-agent#run-it-locally

**How did you use FalkorDB in your project?**
> FalkorDB does all of ShutOff's reasoning. The agent only calls a fixed set of tools (also served over MCP) and never
> writes Cypher.
> - Isolation area: `algo.WCC` with `nodeLabels` over Junction/Pipe/Seized nodes. Operable valves are left out, so
>   they act as walls. The valves to close are the ones bordering the break's component.
> - Safe what-ifs: each plan, and each "what if this valve won't close" case, runs on a `GRAPH.COPY` sandbox. The
>   closed valves are deleted and WCC is re-run; any component with no Source is dry. What-ifs never touch the live
>   graph.
> - Evidence: `algo.SPpaths` returns the route water still takes to the break when a naive plan fails, and each
>   hospital's remaining supply path.
> - State and memory: crew reports become labels (`:Seized`, `:Open`), so the same WCC query re-plans. Approved work
>   orders are written as WorkOrder nodes with CLOSES and NOTIFIES relationships.
> - Restore safety and valve-exercise ranking: on one copy, toggle `:Open` per valve and re-run WCC (96 scenarios in
>   about 0.4 s).
> - Checked: the segments match an independent union-find on the demo network, and US EPA WNTR's `valve_segments` on
>   the public EPANET networks Net1, Net3 and ky4 (964 nodes, 1,158 links). All 12 configurations and all 60
>   seized-valve trials were identical. A full plan with 4 what-if simulations takes about 0.5 s locally.
> - Optional live model mode (Groq `openai/gpt-oss-20b`, live-tested): the model only picks tools. Plan facts come from
>   the FalkorDB results, and approval stays with the human.

**Blog link:** optional. Leave it blank unless you publish a post.

**Feedback.** These are build facts the participant may draw on, in their own words:
- *What we liked:*
  - `algo.WCC` with `nodeLabels` made the isolation area one query, and a seized valve is just a label change.
  - `GRAPH.COPY` gave cheap sandboxes: about 100 ms per full simulation, including the copy.
  - `algo.SPpaths` produced readable evidence paths.
  - One Docker container also includes the Browser UI.
- *Improvements:*
  - `GRAPH.COPY` sometimes failed when it ran at the same time as Redis's background save (fork contention), so we
    added retries.
  - A what-if overlay that avoids copying the whole graph would scale better to city-sized networks.
  - More worked examples for the algorithm procedure options would help.

**Limitations to state honestly:**
- Connectivity only.
- Synthetic town; segmentation is validated on public EPANET topologies with generated valve locations.
- Simulated dispatch and notices.
- One local operator, with no authentication.
- The deterministic parser covers limited phrasings.
- Groq mode was live-tested on one scripted incident and is limited by the free tier's rate limits.
- Claude mode is mock-tested only.
- There is no hosted deployment.

**AI disclosure:** `docs/AI_DISCLOSURE.md`. The participant section is the participant's own statement.
