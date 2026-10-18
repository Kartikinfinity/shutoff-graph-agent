# AI assistance disclosure (Rule 11)

**Tool used:** Claude Code (Anthropic), model Claude Opus 5.5, used as research assistant and coding assistant.

**What the AI assistant did (17–18 Oct 2026):**
- Read the official hackathon pages and guide, audited earlier research, and proposed project ideas with a comparison.
- Wrote the code in this repository: the synthetic network generator, FalkorDB loader, Cypher queries, planning and simulation logic, tool catalogue, agent loop, MCP server, web UI and tests.
- Drafted the README and docs, and scripted the screenshot and demo-video capture.
- Ran a forensic audit of its own implementation (`AUDIT.md`), found safety and correctness bugs, and fixed them with regression tests.
- On 18 Oct, took an independent review (GPT, supplied by the participant) and its own targeted research, fixed the confirmed findings, redesigned the UI, and added the WNTR benchmark (`RESEARCH_AND_REDESIGN.md`).
- On 18 Oct (final sprint), wrote and produced the narrated demo video (script, recording through the real UI, synthetic narration with the open-source Piper TTS and a public-domain-trained voice, captions, mixing) and its automated checks; added the one-command Docker app service and a real MCP stdio test.

**What the participant did:**
- Chose and approved the project direction (ShutOff, Track 1) from the proposed options.
- _Fill in honestly before submitting. Examples: ran the app and tests locally; reviewed the graph model and queries; changed X; recorded the narration; wrote the blog post._

**Verification:**
- The test suite runs against a real FalkorDB instance. Results are whatever `python -m pytest -q` prints on your machine; no results are hard-coded.
- The LLM tool loop is tested with a mocked client only, unless a real API key was used. If you run it live, note that here.
- Simulated parts (crew dispatch, customer notices) are labelled as simulated in the UI, the API responses and the README.
