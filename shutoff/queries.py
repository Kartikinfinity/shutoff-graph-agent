"""Every Cypher query ShutOff runs, in one place.

The agent never writes Cypher. It picks a tool; the tool runs one or more of
these fixed, parameterised queries. Model-supplied names are resolved against
the graph first, so no model text is ever concatenated into a query.
"""

# --- Name resolution -------------------------------------------------------

STREET_NAMES = """
MATCH (p:Pipe)
RETURN DISTINCT p.street AS street
"""

PIPE_BETWEEN = """
MATCH (p:Pipe {street: $street})
WHERE $a IN p.crosses AND $b IN p.crosses
RETURN p.id AS id, p.label AS label
"""

PIPES_ON_STREET_TOUCHING = """
MATCH (p:Pipe {street: $street})
WHERE $a IN p.crosses OR $b IN p.crosses
RETURN p.id AS id, p.label AS label
ORDER BY id
"""

PIPES_ON_STREET = """
MATCH (p:Pipe {street: $street})
RETURN p.id AS id, p.label AS label
ORDER BY id
"""

PIPE_BY_ID = """
MATCH (p:Pipe {id: $id})
RETURN p.id AS id, p.label AS label, p.street AS street, p.material AS material,
       p.installed AS installed, p.diameter_mm AS diameter_mm
"""

CUSTOMER_SEARCH = """
MATCH (c:Customer)-[:SERVED_BY]->(p:Pipe)
WHERE toLower(c.name) CONTAINS toLower($q)
RETURN c.id AS id, c.name AS name, c.kind AS kind, c.critical AS critical,
       p.id AS pipe, p.label AS pipe_label
ORDER BY c.critical DESC, c.id
LIMIT 5
"""

VALVE_BY_ID = """
MATCH (v:Valve {id: $id})
RETURN v.id AS id, v.location AS location, v.status AS status, v.condition AS condition,
       v.last_exercised AS last_exercised, labels(v) AS labels
"""

# --- Isolation (live graph, read-only) ------------------------------------
# A segment is a connected component of pipes and junctions once every
# operable valve is treated as a wall. Seized valves cannot act as walls, so
# they are included as pass-through nodes via the :Seized label.

SEGMENT_OF_PIPE = """
CALL algo.WCC({nodeLabels: ['Junction', 'Pipe', 'Seized'], relationshipTypes: ['CONNECTS']})
YIELD node, componentId
WITH componentId, collect(node) AS members
WHERE any(m IN members WHERE m:Pipe AND m.id = $pipe)
UNWIND members AS m
RETURN m.id AS id, labels(m)[0] AS kind
"""

SEGMENT_OF_NODE = """
CALL algo.WCC({nodeLabels: ['Junction', 'Pipe', 'Seized'], relationshipTypes: ['CONNECTS']})
YIELD node, componentId
WITH componentId, collect(node) AS members
WHERE any(m IN members WHERE m.id = $node)
UNWIND members AS m
RETURN m.id AS id, labels(m)[0] AS kind
"""

VALVE_NEIGHBOURS_ONE = """
MATCH (v:Valve {id: $id})-[:CONNECTS]-(n)
RETURN collect(n.id) AS neighbours
"""

ALL_SEGMENTS = """
CALL algo.WCC({nodeLabels: ['Junction', 'Pipe', 'Seized'], relationshipTypes: ['CONNECTS']})
YIELD node, componentId
RETURN node.id AS id, labels(node)[0] AS kind, componentId AS segment
"""

BOUNDARY_VALVES = """
MATCH (m)-[:CONNECTS]-(v:Valve)
WHERE m.id IN $members AND NOT v:Seized AND NOT v.id IN $members
RETURN DISTINCT v.id AS id, v.location AS location, v.status AS status,
       v.condition AS condition, v.last_exercised AS last_exercised
ORDER BY id
"""

VALVE_NEIGHBOURS = """
MATCH (v:Valve)-[:CONNECTS]-(n)
RETURN v.id AS id, v.location AS location, v.condition AS condition,
       v.last_exercised AS last_exercised, v.status AS status,
       collect(n.id) AS neighbours
"""

CUSTOMERS_ON_PIPES = """
MATCH (c:Customer)-[:SERVED_BY]->(p:Pipe)
WHERE p.id IN $pipes
RETURN c.id AS id, c.name AS name, c.kind AS kind, c.accounts AS accounts,
       c.critical AS critical, p.id AS pipe
ORDER BY c.critical DESC, c.id
"""

CRITICAL_CUSTOMERS = """
MATCH (c:Customer {critical: true})-[:SERVED_BY]->(p:Pipe)
RETURN c.id AS id, c.name AS name, c.kind AS kind, p.id AS pipe
ORDER BY c.id
"""

# --- Simulation (sandbox copy of the graph) --------------------------------
# The sandbox is a GRAPH.COPY of the live network. Closing a valve there means
# deleting it, so every later traversal and algorithm naturally respects it.

SIM_APPLY_CLOSURES = """
MATCH (v:Valve)
WHERE v.id IN $close OR v.status = 'closed'
DETACH DELETE v
"""

SIM_DRY_PIPES = """
CALL algo.WCC({nodeLabels: ['Source', 'Junction', 'Pipe', 'Valve'], relationshipTypes: ['CONNECTS']})
YIELD node, componentId
WITH componentId, collect(node) AS members
WHERE none(m IN members WHERE m:Source)
UNWIND members AS m
WITH m WHERE m:Pipe
RETURN m.id AS pipe
"""

SIM_SUPPLY_PATH = """
MATCH (s:Source), (t {id: $target})
CALL algo.SPpaths({sourceNode: s, targetNode: t, relTypes: ['CONNECTS'],
                   relDirection: 'both', pathCount: 1})
YIELD path, pathWeight
RETURN s.name AS source, [n IN nodes(path) | n.id] AS path, pathWeight AS hops
ORDER BY hops
LIMIT 1
"""

# Used by the valve-exercise ranking: one sandbox, labels toggled per scenario.
SIM_UNSET_OPEN = "MATCH (v:Valve) WHERE v.id IN $ids REMOVE v:Open"
SIM_SET_OPEN = "MATCH (v:Valve) WHERE v.id IN $ids SET v:Open"
SIM_DRY_PIPES_BY_LABEL = """
CALL algo.WCC({nodeLabels: ['Source', 'Junction', 'Pipe', 'Open'], relationshipTypes: ['CONNECTS']})
YIELD node, componentId
WITH componentId, collect(node) AS members
WHERE none(m IN members WHERE m:Source)
UNWIND members AS m
WITH m WHERE m:Pipe
RETURN m.id AS pipe
"""

# --- Actions (live graph, writes) ------------------------------------------

REPORT_VALVE = """
MATCH (v:Valve {id: $id})
SET v.condition = $condition, v.reported_at = $at, v.reported_note = $note
RETURN v.id AS id, v.condition AS condition
"""
MARK_SEIZED = "MATCH (v:Valve {id: $id}) SET v:Seized"
CLEAR_SEIZED = "MATCH (v:Valve {id: $id}) REMOVE v:Seized"

COUNT_WORK_ORDERS = "MATCH (w:WorkOrder) RETURN count(w) AS n"

CREATE_WORK_ORDER = """
MATCH (p:Pipe {id: $pipe})
CREATE (i:Incident {id: $incident, at: $at, status: 'isolated', summary: $report, synthetic: false})-[:ON]->(p)
CREATE (w:WorkOrder {id: $wo, at: $at, status: 'issued', dispatch: 'simulated',
                     accounts_out: $accounts_out, critical_out: $critical_out})-[:FOR]->(i)
RETURN w.id AS id
"""

WORK_ORDER_CLOSE_VALVES = """
MATCH (w:WorkOrder {id: $wo})
UNWIND range(0, size($valves) - 1) AS k
MATCH (v:Valve {id: $valves[k]})
CREATE (w)-[:CLOSES {seq: k + 1}]->(v)
SET v.status = 'closed'
REMOVE v:Open
RETURN count(v) AS closed
"""

WORK_ORDER_NOTIFY = """
MATCH (w:WorkOrder {id: $wo})
UNWIND $notices AS n
MATCH (c:Customer {id: n.id})
CREATE (w)-[:NOTIFIES {priority: n.priority, channel: 'simulated'}]->(c)
RETURN count(c) AS notified
"""

RESTORE_WORK_ORDER = """
MATCH (w:WorkOrder {id: $wo})-[:CLOSES]->(v:Valve)
SET v.status = 'open', v:Open, w.status = 'restored', w.restored_at = $at
WITH DISTINCT w
MATCH (w)-[:FOR]->(i:Incident)
SET i.status = 'resolved'
RETURN w.id AS id, i.id AS incident
"""

OPEN_WORK_ORDERS = """
MATCH (w:WorkOrder {status: 'issued'})-[:FOR]->(i:Incident)-[:ON]->(p:Pipe)
OPTIONAL MATCH (w)-[c:CLOSES]->(v:Valve)
RETURN w.id AS id, w.at AS at, i.id AS incident, p.id AS pipe, p.label AS pipe_label,
       w.accounts_out AS accounts_out, w.critical_out AS critical_out,
       collect(v.id) AS valves
ORDER BY id
"""

PIPE_HISTORY = """
MATCH (i:Incident)-[:ON]->(p:Pipe {id: $pipe})
RETURN i.id AS id, i.at AS at, i.status AS status, i.summary AS summary,
       p.material AS material, p.installed AS installed
ORDER BY at DESC
"""

# --- Map snapshot for the UI -----------------------------------------------

SNAPSHOT_NODES = """
MATCH (n) WHERE n:Source OR n:Junction OR n:Pipe OR n:Valve
RETURN n.id AS id, labels(n) AS labels, n.name AS name, n.label AS label,
       n.x AS x, n.y AS y, n.ax AS ax, n.ay AS ay, n.bx AS bx, n.by AS by,
       n.status AS status, n.condition AS condition, n.location AS location,
       n.last_exercised AS last_exercised, n.material AS material, n.installed AS installed,
       n.street AS street, n.pipe AS pipe, n.kind AS source_kind, n.diameter_mm AS diameter_mm
"""

SNAPSHOT_EDGES = """
MATCH (a)-[:CONNECTS]->(b)
RETURN a.id AS a, b.id AS b
"""

SNAPSHOT_CUSTOMERS = """
MATCH (c:Customer)-[:SERVED_BY]->(p:Pipe)
RETURN c.id AS id, c.name AS name, c.kind AS kind, c.accounts AS accounts,
       c.critical AS critical, p.id AS pipe
"""
