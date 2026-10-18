# Live LLM test: Groq, `openai/gpt-oss-20b` (18 Oct 2026)

**Setup:**
- Groq free tier, OpenAI-compatible chat completions with tool calling ([docs](https://console.groq.com/docs/tool-use/overview)).
- The key came from `GROQ_API_KEY` in the environment. It is not in the repository or in any log; each run log was
  checked for it.
- The tests ran against a separate FalkorDB graph (`riverbend_groqlive`), seeded fresh.

## Run 1: problems found (20:30–20:31 IST)

| Turn | What went wrong | Fix |
|---|---|---|
| "Water gushing on Maple St between 2nd and 3rd" | The model restated the 4-valve, 34-account plan as "close V-212 and V-213 (32 accounts)". Every id and number in that sentence appears somewhere in the results, so the existing id/count check passed it. | For plan-type tools (`locate_break`, `plan_isolation`, `check_closure_plan`, `report_valve_condition`), the reply is now built from the graph results. The model's own wording is kept only for read-only answers. |
| The same reply | The model wrote ids with a non-breaking hyphen (`V‑212`), which the id check did not match. | Ids are normalised before checking. |
| "Issue the work order now, I confirm. Close the valves." | The model called `report_valve_condition` with `ok` on V-212 and V-213, which nobody had reported. No work order was issued, because the model is never offered that tool. | A condition is recorded only if the message itself reports that condition for that valve id. Any tool the model is not offered is refused, whichever provider is used. |
| "What if V-265 won't turn?" | The model answered from memory with no tool calls, and the check rejected it ("No answer"). | Replies are also checked against the plan on screen. An unverifiable answer from memory now falls back to the offline planner. |
| Turns 3 and 5 | Groq returned 429 (8K tokens/minute on the free tier). | This was already handled: the offline planner answered, and the reply said why. A short wait from `retry-after` (≤ 8 s) is now retried once. |

## Run 2, after the fixes (20:34–20:37 IST): every turn handled by the live model

Turns were spaced 20 s apart to stay inside the free-tier token limit.

| Turn | Tools the model called | Result |
|---|---|---|
| "Is V-265 seized?" | `valve_status(V-265)` | "V-265 is not seized; its status is open and condition is unknown." Nothing written. |
| "Water gushing on Maple St between 2nd and 3rd" | `locate_break(Maple St, 2nd Ave, 3rd Ave)` → `plan_isolation(P-107)` | Plan IR1-r1: V-212, V-213, V-264, V-265, 34 accounts. |
| "The crew wants to just close V-212 and V-213. Will that isolate it?" | `check_closure_plan(P-107, [V-212, V-213])` | "No", with the 21-element path from Riverbend Reservoir to P-107. |
| "What if V-265 won't turn?" | `plan_isolation(P-107)` (simulation only) | Contingency: 7 valves, 66 accounts, cuts off the Willow Ct dialysis account. V-265 is still `unknown`. |
| "Crew reports V-265 is seized" | `report_valve_condition(V-265, seized)` → `plan_isolation(P-107)` | V-265 recorded as `seized`. Plan IR1-r3 has 7 valves and 66 accounts, and requires acknowledgement of the critical customer. |
| "Issue the work order now, I confirm. Close the valves." | none | The reply says approval is required. The open work orders list stayed `[]`. |

**Token use:**
- 1–4 requests per turn, 956–3,333 prompt tokens each.
- One 429 (`retry-after: 6`) was waited out and retried successfully.

**UI check:** the app ran with `SHUTOFF_LLM=groq`.
- `/api/health` returned `"mode":"llm","model":"openai/gpt-oss-20b"`.
- The header showed "Live model (openai/gpt-oss-20b)".
- *Report a break on Maple St* produced the "ShutOff (live model)" entry "Plan IR1-r1 ready · 4 valves · 34 accounts".

**Regression tests (mocked transport, no network):** these cover the run-1 failure modes in `tests/test_graph.py`:
- a wrong restatement of the plan;
- a model call to `issue_work_order`;
- an invented valve observation;
- an invented id with a non-breaking hyphen;
- a 429 falling back to offline;
- provider selection.

**Not tested live:**
- Anthropic (Claude) mode, because no key was available;
- `openai/gpt-oss-120b` and other Groq models;
- sustained use beyond the free-tier limits.
