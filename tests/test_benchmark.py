"""Optional: ShutOff's segment query vs WNTR's valve_segments on EPANET Net3.

Skipped unless WNTR is installed (pip install -r requirements-benchmark.txt)."""

import pytest
from conftest import needs_db

wntr = pytest.importorskip("wntr")

pytestmark = needs_db


@pytest.mark.parametrize("placement,n", [("strategic", 1), ("strategic", 2), ("random", 0.25)])
def test_net3_segments_match_wntr(placement, n):
    from benchmarks.wntr_crosscheck import check

    r = check("Net3", placement, n, seized_trials=3)
    assert r["partition_equal"], r
    assert all(t["equal"] for t in r["seized_trials"]), r
