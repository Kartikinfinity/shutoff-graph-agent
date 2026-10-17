import os
import sys
from pathlib import Path

# Tests use their own graph so they never touch the demo graph.
os.environ["SHUTOFF_GRAPH"] = "riverbend_test"
os.environ["SHUTOFF_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from shutoff import db as dbm  # noqa: E402
from shutoff import seed  # noqa: E402


def _falkordb_up() -> bool:
    try:
        dbm.db().list_graphs()
        return True
    except Exception:
        return False


FALKOR = _falkordb_up()
needs_db = pytest.mark.skipif(not FALKOR, reason="FalkorDB is not reachable on localhost:6379 (docker compose up -d)")


@pytest.fixture()
def fresh():
    """A freshly seeded test graph for every test that changes state."""
    seed.load()
    yield
    for g in dbm.db().list_graphs():
        if g.startswith("riverbend_test__sim_"):
            dbm.graph(g).delete()


def pytest_sessionfinish(session, exitstatus):
    if FALKOR:
        try:
            dbm.graph("riverbend_test").delete()
        except Exception:
            pass
