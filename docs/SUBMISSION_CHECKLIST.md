# Submission checklist: Graph Hacks (deadline 18 Oct 2026, 11:59 PM IST; target 8 PM IST)

Required by Rule 10:

- [ ] **Public source-code repository** (GitHub). Push this folder; make sure `.venv/` is not committed.
- [x] **README with setup instructions and the graph data model** (`README.md`)
- [x] **Cypher queries / graph algorithms the product depends on** (README section + `shutoff/queries.py`)
- [ ] **Demo video showing the working project** (`demo/shutoff-demo.webm`, upload to YouTube unlisted or Drive and link it)
- [x] **Local setup instructions** (README "Run it locally"); live deployment optional
- [ ] **Track named in the submission form:** *Agents That Act on Connected Data*

Rules to double-check:

- [x] FalkorDB is the primary database and central (Rules 2–3): all planning runs as graph queries
- [x] Only synthetic data (Rule 6): `shutoff/town.py`
- [x] Main work started after 15 Oct (Rule 8)
- [ ] AI use disclosed (Rule 11): finish the participant section in `docs/AI_DISCLOSURE.md`
- [ ] You can explain the graph model, queries and architecture (Rule 12): read `docs/EXPLAINER.md`, run the demo yourself once
- [ ] Every team member registered on the WeMakeDevs page

Before you submit:

- [ ] `docker compose up -d`, `python -m shutoff.seed`, `python -m pytest -q` (49 core tests; +3 if WNTR is installed) all pass on your machine, with no integration tests skipped
- [ ] Run the demo once yourself in the browser (http://localhost:8000)
- [ ] Watch the demo video end to end
- [ ] Optional: blog post for the AirPods side prize (write it in your own words)
