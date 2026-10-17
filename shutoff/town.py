"""Riverbend: a small, fully synthetic water distribution network.

Everything here is invented for the demo (no real utility, people or
addresses). The layout is a 5 x 6 street grid fed by a reservoir in the west
and an elevated tank in the east, plus one dead-end cul-de-sac (Willow Ct).

Valves sit on pipe legs next to junctions. A few legs deliberately have no
valve, which is common in older networks and is what makes some isolation
segments larger than a single pipe.

This module is pure Python (no database). seed.py loads it into FalkorDB.
"""

from __future__ import annotations

STREETS = ["River Rd", "Maple St", "Oak St", "Pine St", "Cedar St"]  # rows, north -> south
AVENUES = ["1st Ave", "2nd Ave", "3rd Ave", "4th Ave", "5th Ave", "6th Ave"]  # cols, west -> east

X0, DX = 150, 170
Y0, DY = 90, 140

# Legs with NO valve, as (pipe key, junction id at that end).
MISSING_VALVES = {
    ("H-1-1", "J-12"),   # Maple St 2nd-3rd: no valve at Maple & 3rd
    ("V-2-1", "J-12"),   # 3rd Ave Maple-Oak: no valve at Maple & 3rd
    ("H-2-2a", "J-22"),  # Oak St 3rd-Willow: no valve at Oak & 3rd
    ("H-2-2a", "J-WT"),  # Oak St 3rd-Willow: no valve at the Willow Ct tee
    ("WILLOW", "J-WE"),  # Willow Ct dead end: nothing to isolate beyond it
    # Older south-west district: a big multi-pipe segment.
    ("H-4-1", "J-42"),
    ("H-4-2", "J-42"),
    ("V-2-3", "J-42"),
    # North-east corner.
    ("H-0-3", "J-04"),
    ("V-4-0", "J-04"),
}

# Valves whose condition is not known (not exercised for years).
UNKNOWN_CONDITION = {
    ("V-2-1", "J-22"): "2011-04",  # 3rd Ave at Oak St, the demo's weak link
    ("H-4-0", "J-41"): "2012-09",
    ("V-3-2", "J-33"): "2013-06",
    ("H-0-4", "J-05"): "2014-02",
}

MATERIAL_BY_STREET = {
    "River Rd": ("cast iron", 1962),
    "Maple St": ("cast iron", 1958),
    "Oak St": ("ductile iron", 1984),
    "Pine St": ("ductile iron", 1988),
    "Cedar St": ("PVC", 2006),
    "Willow Ct": ("PVC", 1999),
}


def _jid(r: int, c: int) -> str:
    return f"J-{r}{c}"


def _jxy(r: float, c: float) -> tuple[float, float]:
    return X0 + c * DX, Y0 + r * DY


def _cross(node_name: str, street: str) -> str:
    """'Maple St & 3rd Ave' seen from a pipe on '3rd Ave' -> 'Maple St'."""
    parts = [p.strip() for p in node_name.split("&")]
    others = [p for p in parts if p != street]
    return others[0] if others else node_name


def build() -> dict:
    """Return the full network as plain dicts: sources, junctions, pipes,
    valves, links (CONNECTS edges), customers, incidents."""
    junctions: dict[str, dict] = {}
    for r, street in enumerate(STREETS):
        for c, ave in enumerate(AVENUES):
            x, y = _jxy(r, c)
            junctions[_jid(r, c)] = {
                "id": _jid(r, c), "name": f"{street} & {ave}", "x": x, "y": y,
            }
    tx, ty = _jxy(2, 2.5)
    junctions["J-WT"] = {"id": "J-WT", "name": "Oak St & Willow Ct", "x": tx, "y": ty}
    junctions["J-WE"] = {"id": "J-WE", "name": "Willow Ct (dead end)", "x": tx, "y": ty + 85}

    sources = [
        {"id": "S-RES", "name": "Riverbend Reservoir", "kind": "reservoir",
         "x": 40, "y": _jxy(1, 0)[1]},
        {"id": "S-TANK", "name": "Hilltop Tank", "kind": "tank",
         "x": _jxy(3, 5)[0] + 115, "y": _jxy(3, 5)[1]},
    ]

    # Pipe skeleton: (key, street, crosses, end_a, end_b, diameter_mm)
    skeleton: list[tuple] = []
    for r, street in enumerate(STREETS):
        for c in range(len(AVENUES) - 1):
            if r == 2 and c == 2:  # Oak St 3rd-4th is split by the Willow Ct tee
                skeleton.append(("H-2-2a", street, ["3rd Ave", "Willow Ct"], _jid(2, 2), "J-WT", 150))
                skeleton.append(("H-2-2b", street, ["Willow Ct", "4th Ave"], "J-WT", _jid(2, 3), 150))
                continue
            skeleton.append((f"H-{r}-{c}", street, [AVENUES[c], AVENUES[c + 1]],
                             _jid(r, c), _jid(r, c + 1), 150))
    for c, ave in enumerate(AVENUES):
        for r in range(len(STREETS) - 1):
            skeleton.append((f"V-{c}-{r}", ave, [STREETS[r], STREETS[r + 1]],
                             _jid(r, c), _jid(r + 1, c), 200))
    skeleton.append(("WILLOW", "Willow Ct", ["Oak St", "dead end"], "J-WT", "J-WE", 100))
    skeleton.append(("T-RES", "Reservoir main", ["Riverbend Reservoir", "1st Ave"], "S-RES", _jid(1, 0), 400))
    skeleton.append(("T-TANK", "Tank main", ["Hilltop Tank", "6th Ave"], "S-TANK", _jid(3, 5), 400))

    pos = {**{k: (v["x"], v["y"]) for k, v in junctions.items()},
           **{s["id"]: (s["x"], s["y"]) for s in sources}}
    names = {**{k: v["name"] for k, v in junctions.items()},
             **{s["id"]: s["name"] for s in sources}}

    pipes, valves, links = [], [], []
    pipe_id_by_key: dict[str, str] = {}
    vnum = 201
    for i, (key, street, crosses, a, b, dia) in enumerate(skeleton):
        pid = f"P-{101 + i}"
        pipe_id_by_key[key] = pid
        (ax, ay), (bx, by) = pos[a], pos[b]
        material, installed = MATERIAL_BY_STREET.get(street, ("ductile iron", 1990))
        if street.endswith("main"):
            material, installed = "ductile iron", 1990
        elif street.endswith("Ave"):
            material, installed = "ductile iron", 1979
        label = f"{street}, {crosses[0]} to {crosses[1]}"
        pipes.append({
            "id": pid, "key": key, "street": street, "crosses": crosses, "label": label,
            "diameter_mm": dia, "material": material, "installed": installed,
            "x": (ax + bx) / 2, "y": (ay + by) / 2,
            "ax": ax, "ay": ay, "bx": bx, "by": by,
        })
        for end, (ex, ey), (ox, oy) in ((a, (ax, ay), (bx, by)), (b, (bx, by), (ax, ay))):
            if (key, end) in MISSING_VALVES:
                links.append((end, pid))
                continue
            # Valve sits 24 px from the end node, along the pipe.
            length = max(((ox - ex) ** 2 + (oy - ey) ** 2) ** 0.5, 1)
            vx, vy = ex + (ox - ex) * 24 / length, ey + (oy - ey) * 24 / length
            direction = ("east" if ox > ex else "west") if abs(ox - ex) > abs(oy - ey) else ("south" if oy > ey else "north")
            vid = f"V-{vnum}"
            vnum += 1
            exercised = UNKNOWN_CONDITION.get((key, end))
            valves.append({
                "id": vid, "pipe": pid, "at": names[end],
                "location": f"{street} at {_cross(names[end], street)} ({direction} leg)",
                "size_mm": dia, "status": "open", "normal": "open",
                "condition": "unknown" if exercised else "ok",
                "last_exercised": exercised or f"20{18 + (vnum % 7)}-{1 + (vnum * 5) % 12:02d}",
                "x": round(vx, 1), "y": round(vy, 1),
            })
            links.append((end, vid))
            links.append((vid, pid))

    customers = []
    special = {
        "H-1-1": [("C-BAKERY", "Maple Street Bakery", "business", 1)],
        "H-0-1": [("C-SCHOOL", "Riverbend Elementary School", "school", 1)],
        "H-3-2": [("C-HOSP", "Riverbend General Hospital", "hospital", 1)],
        "H-2-4": [("C-CARE", "Oakview Senior Living", "care_home", 1)],
        "WILLOW": [("C-MED7", "Willow Ct medical-priority account (home dialysis)", "medical_priority", 1)],
    }
    for p in pipes:
        if p["key"].startswith("T-"):
            continue
        homes = 6 + (int(p["id"][2:]) * 7) % 15
        if p["key"] == "WILLOW":
            homes = 14
        block = 100 * (1 + (int(p["id"][2:]) % 4))
        customers.append({
            "id": f"C-{p['id'][2:]}", "name": f"{p['street']} {block} block ({homes} homes)",
            "kind": "residential", "accounts": homes, "critical": False, "pipe": p["id"],
        })
        for cid, name, kind, accounts in special.get(p["key"], []):
            customers.append({
                "id": cid, "name": name, "kind": kind, "accounts": accounts,
                "critical": kind in ("hospital", "care_home", "medical_priority"),
                "pipe": p["id"],
            })

    incidents = [
        {"id": "INC-2024-017", "pipe": pipe_id_by_key["H-1-1"], "at": "2024-02-11", "status": "resolved",
         "summary": "Circumferential crack, cast iron main (synthetic history)"},
        {"id": "INC-2025-004", "pipe": pipe_id_by_key["H-1-1"], "at": "2025-01-23", "status": "resolved",
         "summary": "Main break after freeze (synthetic history)"},
        {"id": "INC-2023-088", "pipe": pipe_id_by_key["H-4-0"], "at": "2023-12-05", "status": "resolved",
         "summary": "Joint leak (synthetic history)"},
    ]

    return {
        "sources": sources,
        "junctions": list(junctions.values()),
        "pipes": pipes,
        "valves": valves,
        "links": links,
        "customers": customers,
        "incidents": incidents,
        "pipe_id_by_key": pipe_id_by_key,
    }


if __name__ == "__main__":
    net = build()
    print({k: len(v) for k, v in net.items() if isinstance(v, list)})
