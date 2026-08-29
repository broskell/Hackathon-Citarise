# LODESTAR — Functional Requirements

*Autonomous Logistics Exception Agent · Citta RISE / Idea2Agent · Problem 06*

This is the working list of what LODESTAR has to do. It's written to be read, not
audited — each requirement maps to real code, noted in *italics*.

## 1. Purpose

Take a raw, messy logistics exception (a carrier email, a webhook, or a photo of a
damaged label), figure out what's wrong, actually fix it with real actions, and then
**prove** it moved by re-reading state. Retry the failures worth retrying, escalate the
ones that aren't, and never claim a win it can't show.

## 2. Who uses it

- **Ops operator** — runs the console, answers the agent when it asks, watches it resolve.
- **The agent** — plans and calls tools on its own; the operator only steps in on a
  clarifying question, an approval, or an escalation.

## 3. What it must do

### Input & extraction
- **FR-1** Accept three input kinds: unstructured email, JSON webhook, and a photo of a
  label. *(`SCENARIOS`, `custom_goal`)*
- **FR-2** Pull the shipment id + exception type out of that mess. Use the cheap regex
  reader when the input is clean; fall back to the LLM only when it's genuinely ambiguous.
  *(`parse_exception`)*
- **FR-3** OCR a label photo with vision, and survive a rate-limited vision call by using a
  cached read. *(`extract_from_document`)*
- **FR-4** Normalise a recovered id to canonical form (`shp2002` → `SHP-2002`, bare `3003`
  → `SHP-3003`) so the next lookup lands. *(`_canonical_sid`)*

### Novel input (not just the three demos)
- **FR-5** Run on an arbitrary operator-pasted artifact it has never seen, choosing tools
  dynamically from the **full** tool set — no per-scenario gating. *(`agent.run_agent`,
  `tools=TOOLS` every turn)*
- **FR-6** Treat custom input as **live-only**: no scripted replay stands in for it, and a
  rate-limit surfaces as an honest notice, never a fabricated result. *(`server.run` custom branch)*

### Confidence & the clarifying question
- **FR-7** Before acting on a custom artifact, judge its own confidence on the key fields
  (shipment id, exception type, destination). *(`assess_ambiguity`, `GET /api/clarify`)*
- **FR-8** When a field is genuinely ambiguous (e.g. "Portland" with no state), **pause and
  ask** with candidate options instead of guessing — and change nothing until answered.
  *(clarify card in `web/main.js`)*
- **FR-9** Fold the operator's answer back into the artifact and resume to a normal,
  verified resolution. *(`resolveClarify` → `startStream`)*

### Diagnose & resolve
- **FR-10** Load authoritative shipment/order/customer state before any change.
  *(`get_shipment`, index-backed)*
- **FR-11** Pick the minimal actions that clear the exception — reroute, or
  validate+update+reschedule+credit, or reassign — no fixed sequence. *(agent policy prompt)*

### Resilience
- **FR-12** Route every state change through one choke point that gives it idempotency, a
  canonical key, and bounded exponential-backoff retry. *(`execute_state_change`, `with_retry`)*
- **FR-13** Classify failures in code: **transient** retries and recovers; **permanent**
  fails fast and forces escalation — a code decision, not a prompt. *(`TransientFailure` /
  `PermanentFailure`)*
- **FR-14** Escalate to a human as a first-class terminal outcome for permanent failures,
  no-remedy cases, or high-value orders. *(`escalate_to_human`)*

### Verify
- **FR-15** After any change, re-read live state and return a field-level before/after diff
  — computed from the read, never echoed from the action. A loop guard makes this
  non-skippable. *(`verify_shipment`, `logistics.diff`, `agent` guard)*

### Notify & prove
- **FR-16** Notify the customer on their preferred channel; real sends only when
  **LIVE ACTIONS** is on, otherwise simulated. *(`notify_customer`)*
- **FR-17** Present the outcome in plain operator language: a labelled step trace with a
  one-line rationale each, raw JSON tucked behind "Details", and a **"Done — and you can
  prove it"** list plus the verified diff. *(`TOOL_PLAIN`, `stepWhy`, `proveList`)*

### Observability
- **FR-18** Stream every step live as it happens, and show a per-run metrics bar (tool
  calls, state changes, retries, LLM turns, duration, engine) and an explicit
  **human-intervention: required / not required** line — on all paths. *(SSE + `renderResult`)*

## 4. Quality bar (non-functional)

- **Never crash a run.** Tools return `{ok, ...}` dicts and never raise; the LLM layer
  fails Gemini → Groq and never raises. *(`llm.chat`, `dispatch`)*
- **Stay cheap.** Small models, deterministic-first extraction, compact context, one tool
  call per turn (`MAX_ITERS=12`), O(1) indexed lookups.
- **Survive the free tier.** Gemini paced to ~5/min; Nominatim, vision, and both LLMs have
  documented fallbacks; a full-outage run replays the *real* tools, clearly labelled.
- **Safe by default.** LIVE ACTIONS off; a financial-approval threshold on `issue_credit`;
  escalation always available.

## 5. Out of scope (for now)

Real carrier/TMS integrations, a persisted multi-shipment store, an approval inbox UI, and
an eval harness over labelled artifacts. The hooks exist; the wiring is future work.

## 6. Done when…

- All three demo scenarios resolve/escalate with a verified diff. ✅
- A novel pasted artifact runs live on the full tool set. ✅
- An ambiguous artifact pauses, asks, and resumes to a verified fix without mutating early. ✅
- `python smoke_test.py` → **10/10**. ✅
