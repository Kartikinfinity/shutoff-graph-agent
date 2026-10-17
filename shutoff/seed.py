"""Load the synthetic Riverbend network into FalkorDB.

    python -m shutoff.seed            # (re)create graph 'riverbend'
    python -m shutoff.seed --graph x  # load into another graph name

Graph model (see README for the diagram):
  (:Source)-[:CONNECTS]-(:Pipe)
  (:Junction)-[:CONNECTS]-(:Pipe)          leg with no valve
  (:Junction)-[:CONNECTS]-(:Valve)-[:CONNECTS]-(:Pipe)
  (:Customer)-[:SERVED_BY]->(:Pipe)
  (:Incident)-[:ON]->(:Pipe)

State labels used by the graph algorithms:
  :Open    valve is open (water passes)      -> supply analysis
  :Seized  valve cannot be operated          -> isolation analysis
"""

from __future__ import annotations

import argparse

from . import db as dbm
from .town import build


def load(graph_name: str | None = None) -> dict:
    name = graph_name or dbm.GRAPH
    g = dbm.graph(name)
    try:
        g.delete()
    except Exception:
        pass  # graph did not exist yet
    g = dbm.graph(name)
    net = build()

    g.query("""UNWIND $rows AS r
               CREATE (:Source {id: r.id, name: r.name, kind: r.kind, x: r.x, y: r.y})""",
            {"rows": net["sources"]})
    g.query("""UNWIND $rows AS r
               CREATE (:Junction {id: r.id, name: r.name, x: r.x, y: r.y})""",
            {"rows": net["junctions"]})
    g.query("""UNWIND $rows AS r
               CREATE (:Pipe {id: r.id, street: r.street, crosses: r.crosses, label: r.label,
                              diameter_mm: r.diameter_mm, material: r.material,
                              installed: r.installed, x: r.x, y: r.y,
                              ax: r.ax, ay: r.ay, bx: r.bx, by: r.by})""",
            {"rows": net["pipes"]})
    g.query("""UNWIND $rows AS r
               CREATE (v:Valve:Open {id: r.id, pipe: r.pipe, at: r.at, location: r.location,
                                     size_mm: r.size_mm, status: r.status, normal: r.normal,
                                     condition: r.condition, last_exercised: r.last_exercised,
                                     x: r.x, y: r.y})""",
            {"rows": net["valves"]})
    for label in ("Pipe", "Valve", "Junction", "Source", "Customer", "Incident", "WorkOrder"):
        try:
            g.query(f"CREATE INDEX FOR (n:{label}) ON (n.id)")
        except Exception:
            pass  # index already exists
    links = [{"a": a, "b": b} for a, b in net["links"]]
    g.query("""UNWIND $rows AS r
               MATCH (a {id: r.a}), (b {id: r.b})
               CREATE (a)-[:CONNECTS]->(b)""", {"rows": links})
    g.query("""UNWIND $rows AS r
               MATCH (p:Pipe {id: r.pipe})
               CREATE (:Customer {id: r.id, name: r.name, kind: r.kind, accounts: r.accounts,
                                  critical: r.critical})-[:SERVED_BY]->(p)""",
            {"rows": net["customers"]})
    g.query("""UNWIND $rows AS r
               MATCH (p:Pipe {id: r.pipe})
               CREATE (:Incident {id: r.id, at: r.at, status: r.status, summary: r.summary,
                                  synthetic: true})-[:ON]->(p)""",
            {"rows": net["incidents"]})
    counts = g.ro_query("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label").result_set
    return {row[0]: row[1] for row in counts}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default=None)
    args = ap.parse_args()
    print(load(args.graph))
