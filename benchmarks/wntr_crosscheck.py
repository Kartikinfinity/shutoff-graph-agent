"""Cross-check ShutOff's FalkorDB segmentation against WNTR on EPANET example networks.

    python -m benchmarks.wntr_crosscheck            # Net3 (default) and ky4
    python -m benchmarks.wntr_crosscheck Net1 Net3

What it does, per network:
  1. Load the EPANET .inp bundled with WNTR (US EPA's Water Network Tool for
     Resilience; EPANET example files are public domain).
  2. Place isolation valves with WNTR's own generator: generate_valve_layer
     'strategic' with n=1 (N-1: every node has a valve on all but one link) and
     n=2 (N-2), plus 'random' layers with valves on a fraction of links, which
     produce large multi-pipe segments.
  3. Load nodes, links and valves into a separate FalkorDB graph using
     ShutOff's schema (EPANET nodes -> :Junction, links -> :Pipe, valves ->
     :Valve between the node they protect and their link).
  4. Run ShutOff's own segment query (queries.ALL_SEGMENTS: algo.WCC over
     Junction/Pipe/Seized) and compare the partition with WNTR's
     metrics.valve_segments, element by element.
  5. Repeat with randomly chosen valves marked :Seized (ShutOff) / removed
     from the valve layer (WNTR), which must merge the same segments.

This validates the segmentation step on public networks with real topology.
It does not validate valve locations (they are generated), hydraulics, or the
rest of ShutOff's workflow on these networks.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

import wntr

from shutoff import db as dbm
from shutoff import queries as Q

LIB = Path(wntr.__file__).parent / "library" / "networks"


def wntr_partition(G, valve_layer) -> dict[str, int]:
    node_seg, link_seg, _ = wntr.metrics.valve_segments(G, valve_layer)
    part = {f"N:{n}": int(s) for n, s in node_seg.items()}
    part.update({f"L:{l}": int(s) for l, s in link_seg.items()})
    return part


def load_into_falkordb(name: str, wn, valve_layer) -> None:
    g = dbm.graph(name)
    try:
        g.delete()
    except Exception:
        pass
    g = dbm.graph(name)
    nodes = [{"id": f"N:{n}"} for n in wn.node_name_list]
    links = [{"id": f"L:{l}"} for l in wn.link_name_list]
    g.query("UNWIND $rows AS r CREATE (:Junction {id: r.id})", {"rows": nodes})
    g.query("UNWIND $rows AS r CREATE (:Pipe {id: r.id})", {"rows": links})
    g.query("CREATE INDEX FOR (n:Junction) ON (n.id)")
    g.query("CREATE INDEX FOR (n:Pipe) ON (n.id)")
    valved = {(str(r.node), str(r.link)) for r in valve_layer.itertuples()}
    valves, direct = [], []
    for lname in wn.link_name_list:
        link = wn.get_link(lname)
        for end in {link.start_node_name, link.end_node_name}:
            if (end, lname) in valved:
                valves.append({"id": f"V:{end}:{lname}", "node": f"N:{end}", "link": f"L:{lname}"})
            else:
                direct.append({"a": f"N:{end}", "b": f"L:{lname}"})
    g.query("""UNWIND $rows AS r
               MATCH (n:Junction {id: r.node}), (p:Pipe {id: r.link})
               CREATE (n)-[:CONNECTS]->(v:Valve:Open {id: r.id, status: 'open'})-[:CONNECTS]->(p)""", {"rows": valves})
    g.query("""UNWIND $rows AS r
               MATCH (n:Junction {id: r.a}), (p:Pipe {id: r.b})
               CREATE (n)-[:CONNECTS]->(p)""", {"rows": direct})


def shutoff_partition(name: str) -> dict[str, int]:
    rows = dbm.run("all_segments", Q.ALL_SEGMENTS, graph_name=name)
    return {r["id"]: r["segment"] for r in rows if r["kind"] in ("Junction", "Pipe")}


def as_sets(part: dict[str, int]) -> set[frozenset]:
    groups: dict[int, set] = {}
    for k, s in part.items():
        groups.setdefault(s, set()).add(k)
    return {frozenset(g) for g in groups.values()}


def check(net: str, placement: str = "strategic", n: int | float = 1, seized_trials: int = 5, seed: int = 7) -> dict:
    wn = wntr.network.WaterNetworkModel(str(LIB / f"{net}.inp"))
    G = wn.to_graph()
    if placement == "random":  # n = fraction of links that get one valve
        n = max(1, int(n * wn.num_links))
    layer = wntr.network.generate_valve_layer(wn, placement, n, seed=seed)
    gname = f"bench_{net.lower()}"
    load_into_falkordb(gname, wn, layer)

    t0 = time.perf_counter()
    ours = shutoff_partition(gname)
    ms = (time.perf_counter() - t0) * 1000
    theirs = wntr_partition(G, layer)
    base_equal = as_sets(ours) == as_sets(theirs) and set(ours) == set(theirs)

    rng = random.Random(seed)
    trials = []
    g = dbm.graph(gname)
    for _ in range(seized_trials):
        idx = sorted(rng.sample(range(len(layer)), k=min(3, len(layer))))
        stuck = layer.iloc[idx]
        ids = [f"V:{r.node}:{r.link}" for r in stuck.itertuples()]
        g.query("MATCH (v:Valve) WHERE v.id IN $ids SET v:Seized", {"ids": ids})
        ours_s = shutoff_partition(gname)
        g.query("MATCH (v:Valve) WHERE v.id IN $ids REMOVE v:Seized", {"ids": ids})
        theirs_s = wntr_partition(G, layer.drop(layer.index[idx]))
        trials.append({"seized": ids, "equal": as_sets(ours_s) == as_sets(theirs_s),
                       "segments": len(as_sets(ours_s))})
    g.delete()
    return {
        "network": net, "placement": f"{placement} n={n}", "nodes": wn.num_nodes, "links": wn.num_links, "valves": len(layer),
        "segments": len(as_sets(ours)), "partition_equal": base_equal, "wcc_ms": round(ms, 1),
        "seized_trials": trials, "all_equal": base_equal and all(t["equal"] for t in trials),
    }


CONFIGS = [("strategic", 1), ("strategic", 2), ("random", 0.5), ("random", 0.25)]

if __name__ == "__main__":
    nets = sys.argv[1:] or ["Net1", "Net3", "ky4"]
    out = [check(net, placement, n) for net in nets for placement, n in CONFIGS]
    print(json.dumps(out, indent=2))
    sys.exit(0 if all(r["all_equal"] for r in out) else 1)
