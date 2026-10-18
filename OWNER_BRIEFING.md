# ShutOff — owner briefing (plain English)

Read this so you can explain ShutOff yourself. About 10 minutes.

## The one-sentence pitch

When a water main breaks, ShutOff works out which valves to close, proves on a copy of the network
that the plan works and who it affects, warns about valves that might fail, and only changes the
network after a human approves.

## The graph model (draw this)

```
Reservoir ── Valve ── Pipe ── Valve ── Junction ── Pipe ── ...
                       ▲
                    Customer (SERVED_BY)
```

- **Junction**: a street corner where pipes meet ("Maple St & 3rd Ave").
- **Pipe**: one block of water main ("Maple St, 2nd to 3rd Ave"). It's a *node*, not a line between two nodes, so a break, a customer or a past incident can point at it.
- **Valve**: a node sitting between a corner and a pipe. Closing a valve = taking that node out of the network. That one design choice makes every later question simple.
- **Source**: the reservoir and the tank, where water comes from.
- **Customer**: homes and buildings, linked to the pipe that serves them. Three are *critical*: the hospital, a care home, and a home-dialysis account on Willow Ct.
- **Labels act as switches.** `:Open` means water can pass this valve. `:Seized` means the crew can't turn it. Flip a label and every later calculation changes.
- **Memory.** Incidents, work orders and seized-valve reports are saved in the graph, so the next plan already knows about them.

All the data is invented (synthetic). There's no real town or real people.

## The two graph algorithms

**WCC (weakly connected components)** answers: *which things are still joined together?*
- **Finding the area to shut off.** Run WCC on corners and pipes only, leaving valves out. Every valve then acts like a wall, and the group containing the broken pipe is the smallest area you can shut off (the *segment*). The valves touching that group are the ones to close. A *seized* valve is left *in*, so the area grows through it. That's why "V-265 is seized" turns a 4-valve plan into a 7-valve plan.
- **Who loses water.** After closing valves, run WCC again. Any group with no reservoir or tank in it is dry. If the broken pipe is in a dry group, the break is isolated. Dry customers outside the planned area are an *unintended* cut-off (Willow Ct).

**SPpaths (shortest path)** answers: *show me the route*.
- If the hospital is still supplied, ShutOff shows the exact route from the tank to the hospital (14 steps). That is proof, not a claim.
- If a crew's plan doesn't work, it shows the exact route water still takes to reach the break (20 steps from the reservoir).

## Graph-copy sandboxing

Before trusting a plan, ShutOff copies the whole network graph (`GRAPH.COPY`, about a tenth of a
second here). It closes the valves *in the copy* by deleting them, runs WCC and SPpaths there, then
deletes the copy. The real network graph is never touched while planning; only crew observations, approved
work orders and restores change it. (Copying briefly forks the
database process; if the database is saving to disk at that exact moment, ShutOff waits and retries.)

## The important queries, one line each

| Query (in `shutoff/queries.py`) | What it does in plain words |
|---|---|
| `PIPE_BETWEEN` | Finds the pipe on "Maple St between 2nd and 3rd" |
| `SEGMENT_OF_PIPE` | WCC: the smallest area around the break that valves can wall off |
| `BOUNDARY_VALVES` | The valves touching that area: the ones to close |
| `SIM_DELETE_CLOSED` + `SIM_DRY_PIPES` | In the copy: apply valves *already* closed by other work orders, see what's already dry (the baseline) |
| `SIM_DELETE_VALVES` + `SIM_DRY_PIPES` | In the copy: close this plan's valves, see what's dry now. The difference is this plan's impact |
| `SIM_SUPPLY_PATH` | SPpaths: the route from a source to a customer, or to the break |
| `SEGMENT_OF_NODE` | Used for "what if this valve won't turn": the area on the valve's other side, to merge in |
| `MARK_SEIZED` | Saves a crew report "V-265 won't turn" as a `:Seized` label |
| `CREATE_WORK_ORDER`, `WORK_ORDER_CLOSE_VALVES`, `WORK_ORDER_NOTIFY` | After approval: record the work order, mark valves closed, record notices (simulated) |
| `REOPEN_VALVES`, `TRANSFER_HOLD` | Restore: re-open valves, except ones another active break still needs, which are handed to that break's work order |

## Impact validation and approval safeguards

1. **The AI can't close valves.** The language model, when used, only gets read tools and "save a crew observation". Closing or re-opening valves needs a human click, or an exact typed command ("approve", "restore WO-1001"). "Do not restore WO-1001" or "How do I restore…" changes nothing.
2. **Questions never write.** "Is V-265 seized?" reads the valve's recorded condition. "What if V-265 won't turn?" answers from the plan's pre-computed contingency. Only a statement such as "Crew reports V-265 is seized" saves anything.
3. **Approval is tied to the plan on screen.** Every plan has a revision ID (`IR1-r1`, `IR1-r2`, …). A new report, even a failed one, clears the old plan. Approving an old revision is refused.
4. **The server re-checks on approval.** It runs the simulation again and refuses if:
   - the break would still get water,
   - a valve in the plan is recorded as seized,
   - the impact (accounts out, critical customers, *which* customers) differs from what was on screen when you approved (the network changed in between).
5. **Critical customers need an explicit tick.** If the plan cuts off the dialysis account, you must tick a box naming it. The server checks the exact customer ID, not just the click.
6. **Fair numbers.** Customers already without water because of *another* work order aren't counted against a new plan; they're shown separately.
7. **Safe restore.** When a repair is done, each valve is re-opened in a copy first. If that would send water back into another break that is still being repaired, the valve stays closed.
6. **No guessing.** An unknown street gets "not in the graph"; an unclear location gets a list of candidate pipes. A crew message only marks a valve seized if it actually says that valve is stuck.

Be precise about the dialysis account: ShutOff can't keep it supplied once V-265 fails, because
there is no other pipe to Willow Ct. What it does is *predict* the risk before the valve fails, flag
it, force an explicit acknowledgement, and record a priority notice (simulated).

## What the tests actually prove (51 core tests + 3 optional benchmark tests, all passing)

- **The algorithm is right.** For every one of the 53 pipes, FalkorDB's WCC answer matches an independent Python calculation. On three public EPANET networks (up to 964 nodes), ShutOff's segment query gives exactly the same segments as the US EPA's WNTR toolkit, including with seized valves (`docs/BENCHMARK_WNTR.md`).
- **The demo numbers are real.** Plan A is 4 valves and 34 accounts. Plan B is 7 valves, 66 accounts, with the dialysis account cut off. The crew's 2-valve plan fails, and every step of the leak path is a real pipe connection.
- **Planning never changes the real network.** No copies are left behind, and read-only queries can't write.
- **The safeguards work.** All of these are refused:
  - seized valves and leaking plans;
  - changed impact, and an out-of-date plan revision;
  - a plan left over from a failed report;
  - a missing or wrong acknowledgement, or missing human confirmation;
  - duplicate valves.

  Questions, what-ifs and negated commands cause no writes. Restore never re-feeds another active break and can't run twice.
- **Inputs aren't guessed.** Unknown and ambiguous locations are handled honestly.
- **The interfaces work.** The offline agent, the web API and the MCP server work end to end. Copying survives clashes with the database's background saves.

What the tests do **not** prove:
- Live Claude mode. It's tested only with a fake client, and has never been run against the real API.
- Behaviour on a real utility network.
- Water pressure.
- How the UI looks; that was checked by driving it with Playwright in both themes and at three screen sizes, then reviewing the screenshots.
- That the deterministic parser understands every phrasing; it handles a limited set.

## Three answers to have ready

- **"Why a graph?"** Every decision here is "what is still connected to what". The graph database answers that directly, and every answer comes with a path as evidence.
- **"How is this different from the official Blast Radius example?"** Blast Radius traverses a dependency graph to answer impact questions and saves findings as memory. ShutOff decides an action, tests it on a copy, checks post-conditions, changes the graph only after approval, and re-plans when a crew reports a stuck valve.
- **"What's simulated?"** The town, the crew dispatch and the customer notices. The graph calculations are real.
