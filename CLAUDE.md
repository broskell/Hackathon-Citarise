# CLAUDE.md — Logistics Exception Agent

Guidance for anyone (human or AI) working in this repo. Read this before editing.

## What this is

An autonomous **Logistics Exception Agent**. Given a *raw* exception artifact — a messy
carrier email, a semi-structured JSON webhook, or a **photo** of a damaged shipping label —
it extracts the shipment, diagnoses the problem, resolves it **dynamically** (reroute /
reschedule / credit / reassign / escalate), and then **proves the fix** by re-reading
authoritative state and showing a field-level before/after diff.

Three things make it more than a scripted demo:
1. **Extraction under ambiguity** — every run starts from unstructured input (incl. vision).
2. **A resilient execution layer** — idempotent mutations, exponential-backoff retry, and a
   code-level *transient (retry)* vs *permanent (escalate)* distinction.
3. **One real external call** — `validate_address` hits live OpenStreetMap Nominatim.

See [DESIGN.md](DESIGN.md) for the full technical design.

## Architecture (data flow)

```
raw artifact ─▶ agent.py loop ──▶ tools.py (schemas + arg handling)
                  │  chat()            │  reads          state changes
                  ▼                    ▼                 ▼
               llm.py             logistics.py (DB, indexes, snapshot/diff,
          Gemini▶Groq failover     idempotency, retry, failure injection)
                  │
                  ▼
               app.py  ── live trace + before/after diff panel (Streamlit)
```

| File | Responsibility |
|------|----------------|
| [llm.py](llm.py) | `chat(messages, tools)` — Gemini primary, Groq failover, provider-agnostic tool calling. **Never raises.** |
| [logistics.py](logistics.py) | The mock system: `DB`, indexes, 3-scenario `SEED` + raw artifacts, `reset_db`, snapshot/`diff`/`is_resolved`, `execute_state_change` (idempotency + retry + failure class). **No LLM, no network, no disk.** |
| [tools.py](tools.py) | Tool functions + JSON schemas (`TOOLS`) + `dispatch()`. The active `TOOLS` list is the logistics toolset; `LIVE_ACTIONS` gate lives here. |
| [agent.py](agent.py) | `run_agent(goal)` — the plan→tool→observe loop, `SYSTEM_PROMPT` (policy), and two deterministic guards. |
| [app.py](app.py) | Streamlit UI: scenario picker, LIVE ACTIONS toggle, live trace, verified diff panel. |
| [make_assets.py](make_assets.py) | Generates `assets/label_SHP-3003.png` (Scenario C's photo). |
| [smoke_test.py](smoke_test.py) | Deterministic PASS/FAIL checks; **no real sends**. |

## Run / test / demo

```bash
pip install -r requirements.txt
python make_assets.py          # one-time: generate the Scenario C label photo
python smoke_test.py           # 10 deterministic checks, no outbound sends
python agent.py A              # headless scenario A (or B / C)
streamlit run app.py           # the demo UI
```

Force the failover live: `FORCE_GEMINI_FAIL=1` (PowerShell: `$env:FORCE_GEMINI_FAIL=1`).

## Rules for writing/editing tools (IMPORTANT — the scaffold has sharp edges)

1. **Never raise.** A tool returns `{"ok": bool, ...}`. `dispatch()` will catch, but design
   for graceful dicts (missing keys → `{"ok": False, "error": ...}`).
2. **Schema allow-list.** `llm._clean_schema` strips every JSON-Schema key except
   `type, description, properties, required, items, enum` (Gemini rejects the rest). Do NOT
   use `additionalProperties`, `default`, `$ref`, `minimum`, etc. Use `enum` to constrain.
3. **State changes go through `logistics.execute_state_change`.** Never mutate `DB` directly
   from a tool. Pass a `mutate_fn` (applies the change, returns the changed fields) and a
   **canonical idempotency key** derived from the semantic args (e.g. `reroute:SHP-1001:RapidEx`).
   This gives you idempotency + retry + failure-class handling for free.
4. **Real side effects are gated.** Anything that contacts a person must respect
   `tools.LIVE_ACTIONS` (see `notify_customer`). DB mutations are always local/safe.
5. **One tool call per turn.** The loop runs `tool_calls[0]`; keep flows sequential.
   `agent.MAX_ITERS = 12` — keep observations compact (`_summarize` truncates at 1200).
6. **Register in both places:** add the function to `DISPATCH`, its schema to `TOOLS` (only
   if the agent should see it), and add state-changers to `STATE_CHANGING`.

## Adding a demo scenario

1. Add a shipment (+ order + customer + any carriers) to `SEED` in `logistics.py`. For a
   rigged failure set `_fail_tool` / `_fail_mode` (`transient`|`permanent`) / `_fail_after`.
2. Add a raw artifact constant and a `SCENARIOS` entry (`source_type`:
   `email` | `webhook_json` | `ocr_text`).
3. That's it — `scenario_goal()`, the UI picker, and the smoke test read from these.

## Gotchas

- **Gemini free-tier quota** is small; heavy runs 429. `chat()` fails over to Groq, and
  `extract_from_document` falls back to a cached `.ocr.txt` sidecar — so the demo survives.
- The agent must make a **real function call**, not narrate one in text. The loop echoes a
  content-free `"OK."` (not `"Called <tool>"`) so the model doesn't imitate a call as prose;
  don't reintroduce a tool-name echo.
- `verify_shipment` is mandatory after a state change (guard enforces it); `notify_customer`
  is nudged before finishing (guard).
