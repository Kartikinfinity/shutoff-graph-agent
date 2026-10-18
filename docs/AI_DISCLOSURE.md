# AI assistance disclosure (Rule 11)

**AI tools used to build ShutOff:**
- **Claude Code** (Anthropic), model Claude Opus 5.5. It did the research, proposed the designs, and wrote almost all
  of the code, tests and documentation, and the demo video.
- **GPT** (OpenAI): one independent review of the build on 18 Oct, which the participant supplied.

**AI used inside the product (optional, at runtime):** the live model mode lets an LLM choose among ShutOff's fixed
tools. The provider is Groq `openai/gpt-oss-20b` (live-tested) or Claude (tested with a mocked client only). The
default mode uses no AI.

**Non-AI tools used for the video:** Piper, an open-source text-to-speech engine, with a voice trained on
public-domain recordings. faster-whisper speech recognition was used only to check the narration.

**What Claude Code did (17–18 Oct 2026):**
- Read the official hackathon pages and guide, audited earlier research, and proposed project ideas with a comparison.
- Wrote the code in this repository: the synthetic network generator, FalkorDB loader, Cypher queries, planning and
  simulation logic, tool catalogue, agent loops, MCP server, web UI and tests.
- Wrote the README and docs, and scripted the screenshot and demo-video capture.
- Ran a forensic audit of its own implementation (`AUDIT.md`), found safety and correctness bugs, and fixed them with
  regression tests.
- On 18 Oct, worked through the independent GPT review and its own targeted research. It fixed the confirmed findings,
  redesigned the UI and added the WNTR benchmark (`RESEARCH_AND_REDESIGN.md`).
- In the final sprint on 18 Oct:
  - produced the narrated demo video (script, recording through the real UI, synthetic narration, captions, mixing)
    and its automated checks;
  - added the one-command Docker app service and a real MCP stdio test;
  - added the Groq provider and ran the live test (`docs/LIVE_LLM_TEST.md`), fixing the safety gaps it exposed;
  - built the optional one-container hosting image (`deploy/`).
- Made the git commits from the participant's machine. They are recorded under the team's configured git identity,
  with a `Co-Authored-By: Claude` trailer.

**AI Assistance and Participant Contribution**

Claude (Claude Code, model Claude Opus 5.5) was used extensively as a development assistant for ShutOff. It did the research, proposed the designs, generated and debugged the code, wrote the regression tests and documentation, and produced the demo video. Most of the code and documentation in this repository was written by Claude under my direction. I also obtained an independent review of the build from GPT, and its confirmed findings were fixed.

My contribution was setting the direction and making the decisions. I chose the hackathon track (Track 1, Agents That Act on Connected Data). I chose ShutOff from the project options Claude researched and proposed, and I set the functional and usability priorities for the operator workflow. I decided which findings and changes to pursue, created the public repository, and approved each push to it.

I made the final product and submission decisions. The project's claims and limitations are stated explicitly, including its synthetic water network, connectivity-only analysis and simulated operational actions.

FalkorDB is central to the project's graph-based network reasoning. I am responsible for being able to explain the graph model, the main Cypher queries, the architecture and the safety constraints, and I am reviewing them using `docs/EXPLAINER.md`.

**Verification:**
- The test suite runs against a real FalkorDB instance. Results are whatever `python -m pytest -q` prints; no results
  are hard-coded.
- LLM mode: Groq `openai/gpt-oss-20b` was live-tested on one scripted incident (`docs/LIVE_LLM_TEST.md`). Claude mode is
  tested with a mocked client only.
- The demo video's checks are automated (decoding, loudness, caption sync, speech recognition).
- Simulated parts (crew dispatch, customer notices) are labelled as simulated in the UI, the API responses and the
  README.
