# Segmentation cross-check against WNTR (EPANET example networks)

Run on 18 Oct 2026 with `python -m benchmarks.wntr_crosscheck` (WNTR 1.5.0, FalkorDB graph module 4.22.1).

**Question:** does ShutOff's isolation-segment query (`queries.ALL_SEGMENTS`: `algo.WCC` over `Junction`/`Pipe`/`Seized`,
with operable valves left out as walls) partition a real network topology the same way as an independent implementation?

**Independent implementation:** `wntr.metrics.valve_segments` from the US EPA's Water Network Tool for Resilience (WNTR).
It removes valved links, labels connected components, and re-attaches links with one valve.

**Networks:** EPANET example files shipped with WNTR. Net1 and Net3 are the classic EPANET examples (Net3 is based on the
North Marin Water District, Novato, CA). ky4 is a Kentucky benchmark network. Pipe topology is real; **valve locations are
generated** by WNTR's own `generate_valve_layer` ('strategic' N-1 / N-2 rules, or random placement on a fraction of links).

**Seized trials:** in each configuration, 5 random sets of 3 valves are marked `:Seized` in FalkorDB and dropped from
WNTR's valve layer. Both sides must merge exactly the same segments.

| Network | Valve layer | Nodes | Links | Valves | Segments | Partition identical | Seized trials identical | WCC time |
|---|---|---|---|---|---|---|---|---|
| Net1 | strategic n=1 | 11 | 13 | 15 | 13 | yes | 5/5 | 2.3 |
| Net1 | strategic n=2 | 11 | 13 | 6 | 4 | yes | 5/5 | 1.7 |
| Net1 | random n=6 | 11 | 13 | 6 | 4 | yes | 5/5 | 1.8 |
| Net1 | random n=3 | 11 | 13 | 3 | 2 | yes | 5/5 | 2.1 |
| Net3 | strategic n=1 | 97 | 119 | 141 | 119 | yes | 5/5 | 11.8 |
| Net3 | strategic n=2 | 97 | 119 | 60 | 39 | yes | 5/5 | 4.5 |
| Net3 | random n=59 | 97 | 119 | 59 | 38 | yes | 5/5 | 4.4 |
| Net3 | random n=29 | 97 | 119 | 29 | 16 | yes | 5/5 | 4.4 |
| ky4 | strategic n=1 | 964 | 1158 | 1352 | 1158 | yes | 5/5 | 76.4 |
| ky4 | strategic n=2 | 964 | 1158 | 646 | 457 | yes | 5/5 | 30.7 |
| ky4 | random n=579 | 964 | 1158 | 579 | 405 | yes | 5/5 | 80.4 |
| ky4 | random n=289 | 964 | 1158 | 289 | 159 | yes | 5/5 | 31.5 |

**Result:** identical partitions in all 12 configurations and all 60 seized-valve trials.

**What this does not show:** valve locations are synthetic; ShutOff's other steps (supply/outage analysis,
contingencies, work orders) and its UI were not run on these networks; there is no hydraulic (pressure/flow) analysis.
