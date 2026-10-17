"""FalkorDB connection plus a small query tracer.

Every query the product runs goes through `run()`, which records the query
name, Cypher text, parameters, row count and latency. The UI shows this
trace as the agent's evidence, so nothing is hidden behind a summary.
"""

from __future__ import annotations

import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from falkordb import FalkorDB

GRAPH = os.getenv("SHUTOFF_GRAPH", "riverbend")
HOST = os.getenv("FALKORDB_HOST", "localhost")
PORT = int(os.getenv("FALKORDB_PORT", "6379"))
PASSWORD = os.getenv("FALKORDB_PASSWORD") or None

_db: FalkorDB | None = None


def db() -> FalkorDB:
    global _db
    if _db is None:
        _db = FalkorDB(host=HOST, port=PORT, password=PASSWORD)
    return _db


def graph(name: str | None = None):
    return db().select_graph(name or GRAPH)


@dataclass
class QueryRecord:
    name: str
    cypher: str
    params: dict
    rows: int
    ms: float
    graph: str


@dataclass
class Trace:
    queries: list[QueryRecord] = field(default_factory=list)


_trace: ContextVar[Trace | None] = ContextVar("shutoff_trace", default=None)


def start_trace() -> Trace:
    t = Trace()
    _trace.set(t)
    return t


def current_trace() -> Trace | None:
    return _trace.get()


def run(name: str, cypher: str, params: dict | None = None, *, write: bool = False,
        graph_name: str | None = None) -> list[dict[str, Any]]:
    """Run one named Cypher query and return rows as dicts.

    Reads use ro_query so a read tool can never modify the graph.
    """
    g = graph(graph_name)
    params = params or {}
    t0 = time.perf_counter()
    res = g.query(cypher, params) if write else g.ro_query(cypher, params)
    ms = (time.perf_counter() - t0) * 1000
    header = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
    rows = [dict(zip(header, r)) for r in (res.result_set or [])]
    tr = _trace.get()
    if tr is not None:
        tr.queries.append(QueryRecord(name, _squash(cypher), _jsonable(params), len(rows),
                                      round(ms, 2), graph_name or GRAPH))
    return rows


def _squash(cypher: str) -> str:
    return " ".join(line.strip() for line in cypher.strip().splitlines())


def _jsonable(params: dict) -> dict:
    return {k: (v if isinstance(v, (str, int, float, bool, list, type(None))) else str(v))
            for k, v in params.items()}
