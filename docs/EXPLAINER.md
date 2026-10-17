# ShutOff: how it works, in plain language

Read this before recording the video or talking to judges. Rule 12 requires that you can explain
the graph model, the queries, the architecture and the technical decisions.

## 1. The problem in one breath

A water main breaks. Valves sit on the pipes. To stop the water you close the valves around the
break. You want the **fewest customers** without water. You must **not** accidentally cut off a
hospital or a dialysis patient. And sometimes a valve **won't turn** because it is old and seized.
Deciding this quickly is hard because water networks are **loops**, not trees.

## 2. Why a graph (and not SQL tables)

The questions are all about *what is connected to what*:
- "Which pipes can I isolate together?" means: which pipes stay connected if every valve were a wall?
- "Does the break still get water?" means: is the break still connected to a reservoir or tank?
- "Who loses water?" means: which pipes are no longer connected to any source?

These are graph algorithms (connected components, shortest path). FalkorDB runs them inside the
database with `CALL algo.WCC(...)` and `CALL algo.SPpaths(...)`, in a few milliseconds.

## 3. The graph model (draw this on a whiteboard)

```
(Reservoir)─[CONNECTS]─(Valve)─[CONNECTS]─(Pipe)─[CONNECTS]─(Valve)─[CONNECTS]─(Junction)─ ...
                                             ▲
                                 (Customer)─[SERVED_BY]
```
- **Pipe is a node**, not a relationship. Why? So customers, breaks and incidents can point *at* a pipe, and so the algorithm can include or skip pipes by label.
- **Valve is a node between a pipe and a junction.** Removing the valve node splits the network there. That is exactly what closing a valve does.
- **Labels carry state.** `:Open` = water passes. `:Seized` = can't be closed. Algorithms filter by label, so changing a label changes every later answer.

## 4. The three core queries

1. **Segment**: `algo.WCC` over `Junction`, `Pipe` and `Seized` nodes only. Normal valves are left out, so they act as walls. The component that contains the broken pipe is the area to shut off. A seized valve is *included*, so the area grows through it.
2. **Boundary valves**: every valve touching that component. Those are the valves to close.
3. **Simulation**: copy the whole graph (`GRAPH.COPY`, about 10–70 ms), delete the valves we plan to close, run WCC again. Any component without a `Source` is dry. Then `algo.SPpaths` from each source to the break (should find nothing) and to each critical customer (should find a path, which is the proof).

The copy matters because **planning never touches the real network graph**. Only an approved work
order changes the live graph.

## 5. What the "agent" part is

- A message comes in ("water gushing on Maple St between 2nd and 3rd").
- A planner chooses **tools** from a fixed list: `locate_break`, `plan_isolation`, `check_closure_plan`, `report_valve_condition`, `supply_check`, `valve_exercise_priorities`, `pipe_history`.
- **Offline mode** (default): simple keyword rules choose the tools. Fully deterministic.
- **LLM mode** (when `ANTHROPIC_API_KEY` is set): Claude chooses the tools. Its final sentence is checked. If it mentions a valve ID or a number the graph didn't return, it is thrown away and replaced with a summary built from the tool results.
- The model **never writes Cypher**. It only fills tool arguments, which are validated (e.g. `V-\d{3}`) and looked up in the graph first.
- **Acting**: closing valves happens only when a human presses *Approve*. The server then runs the simulation again. It refuses if the break would still be fed, a valve is seized, or the impact differs from what the human saw. If a critical customer loses water, the human must tick an acknowledgement naming that customer, and the server checks the IDs. Then it writes `WorkOrder`, `CLOSES` and `NOTIFIES` into the graph.
- **Restoring** re-opens valves one at a time on a sandbox first. If re-opening a valve would re-feed another active break, it stays closed and is handed to that break's work order.
- **Memory**: seized valves, incidents and work orders stay in the graph. The next plan uses them, and the next session sees them.

## 6. The demo story

| Step | What happens | What the graph did |
|---|---|---|
| Break on Maple St | 4 valves, 34 accounts | WCC segment = {P-107, J-12, P-136}; sandbox check says isolated |
| Warning | V-265 not exercised since 2011 | Contingency: merge segments through V-265, simulate: dialysis cut off |
| "Just close V-212 and V-213?" | No | SPpaths finds a 20-hop route from the reservoir to the break |
| "V-265 is seized" | 7 valves, 66 accounts, dialysis out | `SET v:Seized`, so WCC grows the segment; Willow Ct has no other source path |
| Acknowledge + Approve | WO-1001 | Server re-simulates, checks the acknowledged customer IDs; valves get `status='closed'` and lose `:Open`; notices recorded as edges |
| Elm St | Not in graph | Street name not found, so nothing runs |

## 7. Likely judge questions

- **"Why not just compute this in Python?"** The network and its state live in FalkorDB. WCC and shortest paths run in the database, and every change (seized valve, closed valve) is a graph write that the next query sees. The UI, the MCP server and tests all share that single source of truth. We do cross-check WCC against a Python union-find, but only as a *test*.
- **"Is this real hydraulics?"** No. It is connectivity. It tells you whether water *can* reach a place, not at what pressure. Utilities would pair it with a hydraulic model. Say so plainly.
- **"How is this different from Blast Radius?"** Blast Radius *reads* an existing dependency graph. ShutOff *decides an intervention*, tests it on a copy, checks safety conditions, writes the change after approval, and re-plans when a crew reports a failure.
- **"What stops the AI from doing something dangerous?"** It has no tool to close valves. A human approves, and the server re-verifies before writing: same impact as shown, no seized valves, the break really isolated, critical customers explicitly acknowledged.
- **"Is the dialysis patient protected?"** Be precise: the network has no other path to Willow Ct, so no valve plan can keep it supplied once V-265 fails. ShutOff *predicts* the risk before it happens, flags it, forces an explicit acknowledgement, and records a priority notice (simulated). It does not magically protect it.
- **"What did you build vs. the AI?"** Answer honestly using `docs/AI_DISCLOSURE.md`.

## 8. Files to know

| File | One line |
|---|---|
| `shutoff/town.py` | Builds the synthetic town (grid, missing valves, Willow Ct dead-end) |
| `shutoff/seed.py` | Loads it into FalkorDB |
| `shutoff/queries.py` | Every Cypher query |
| `shutoff/network.py` | Planning, sandbox simulation, contingencies, actions |
| `shutoff/tools.py` | Tool catalogue + validation (shared by web agent and MCP) |
| `shutoff/agent.py` | Offline planner, Claude tool loop, reply check |
| `shutoff/api.py`, `web/` | Web app |
| `shutoff/mcp_server.py` | MCP server |
| `tests/` | 40 tests |
