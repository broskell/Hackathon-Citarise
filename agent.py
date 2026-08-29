"""Hand-rolled agent loop (no LangChain/LangGraph) specialized as the
Logistics Exception Agent.

    plan -> call tool -> observe -> repeat -> final answer

run_agent(goal) returns:
    {
      "answer": str,
      "trace": [ {step, thought, tool, tool_input, observation, provider}, ... ],
      "iterations": int,
      "state_diff": {shipment_id, diff, resolved, current_state, audit_log} | None,
    }

The trace is exactly what app.py streams into the right-hand panel; state_diff
feeds the persistent before/after panel.

=========================================================================
 PROBLEM LOGIC lives in SYSTEM_PROMPT below + tools.py + logistics.py.
 The loop, provider failover, and trace are generic and unchanged in shape;
 the only task-specific additions here are two deterministic guards (permanent-
 failure short-circuit + verify-before-finish) and tracking the active shipment
 so we can attach the final state diff.
=========================================================================
"""

import json

import logistics
from llm import chat
from tools import STATE_CHANGING, TOOLS, dispatch

MAX_ITERS = 12  # parse + diagnose + act + (retry) + verify + notify + recovery

# ---- PROBLEM LOGIC (system prompt) -------------------------------------
SYSTEM_PROMPT = """You are a Logistics Exception Agent. You resolve shipment exceptions
end-to-end, then PROVE the fix by re-reading state.

You are given a RAW exception artifact -- a carrier email, a JSON webhook, or a photo of
a shipping label. Follow this SPIRIT, but choose tools based on the SITUATION, not a fixed
script:

1. UNDERSTAND THE INPUT FIRST.
   - If the artifact is an image file path, call extract_from_document to OCR it.
   - Call parse_exception on the raw text to get the shipment_id + exception_type.
2. DIAGNOSE. Call get_shipment to load authoritative state before doing anything.
3. RESOLVE -- pick the MINIMAL actions that clear the exception:
   - WEATHER_HOLD : find an alternate carrier (list_carriers), then reroute_shipment.
   - BAD_ADDRESS  : validate_address, then update_address. Because the delivery already
                    failed multiple times, you MUST then reschedule_delivery to get it
                    moving again, and issue_credit a small goodwill credit for the failed
                    attempts.
   - CARRIER_OUTAGE: check list_carriers for a healthy alternate, then ATTEMPT
                    reassign_carrier onto it. If that reassignment fails (failure_class
                    "permanent") or there is no healthy alternate, escalate_to_human.
4. RESILIENCE & ESCALATION.
   - If a state-changing tool returns ok:false, DO NOT repeat the same call. Try a
     different approach, or escalate_to_human.
   - failure_class "permanent" means retrying is useless -- escalate.
   - Escalate when no automated remedy exists, or after a permanent failure. For
     high-value orders (order value >= the escalate threshold), be conservative: if your
     first remedy doesn't cleanly succeed, escalate rather than keep trying.
5. VERIFY -- after ANY state change you MUST call verify_shipment before you notify or
   finish. It returns a before/after diff; confirm the exception is resolved (or properly
   escalated).
6. NOTIFY -- notify_customer on their preferred channel with a short, clear update.

Then give a concise final answer: what was wrong, what you did, and the outcome.
Do not invent tool results. Take ONE action per turn. To take an action you MUST make an
actual tool/function call -- never describe or narrate a call in your text; text with no
function call is treated as your final answer.
"""
# ------------------------------------------------------------------------


def _summarize(obs, limit=1200):
    """Compact a tool observation so long payloads don't blow up context."""
    s = obs if isinstance(obs, str) else json.dumps(obs, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + " ...[truncated]"


def run_agent(goal: str, on_step=None) -> dict:
    """Run the agent loop.

    Args:
        goal: The user's goal / task (typically embeds the raw exception artifact).
        on_step: optional callback(step_dict) invoked after every iteration so a UI
                 can render the trace live.
    """
    # Fresh, deterministic world for this run (also captures the 'before' snapshot).
    logistics.reset_db()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": goal},
    ]
    trace = []
    last_provider = "none"

    # --- guard/track state -------------------------------------------------
    active_shipment = None          # last shipment the agent touched
    permanent_fail = set()          # (tool, shipment) that failed permanently
    pending_verify = False          # a state change happened but wasn't verified yet
    reminded_verify = False         # we only nudge verify-before-finish once
    need_notify = False             # a resolution happened but customer not told yet
    reminded_notify = False         # we only nudge notify-before-finish once

    def finish(answer, step):
        return {
            "answer": answer,
            "trace": trace,
            "iterations": step,
            "state_diff": logistics.final_report(active_shipment) if active_shipment else None,
        }

    for step in range(1, MAX_ITERS + 1):
        result = chat(messages, tools=TOOLS)
        last_provider = result.get("provider", "none")

        if result.get("error"):
            entry = {"step": step, "thought": "LLM call failed on every provider.",
                     "tool": None, "tool_input": None, "observation": result["error"],
                     "provider": last_provider}
            trace.append(entry)
            if on_step:
                on_step(entry)
            return finish(f"Agent stopped: {result['error']}", step)

        tool_calls = result.get("tool_calls") or []
        thought = result.get("text", "").strip()

        # --- No tool call -> the model wants to finish. -------------------
        if not tool_calls:
            # Guard (b): don't let it finish with an unverified state change.
            if pending_verify and not reminded_verify:
                reminded_verify = True
                nudge = ("You changed shipment state but have not called verify_shipment "
                         "yet. Call verify_shipment now to confirm the before/after diff, "
                         "then notify and finish.")
                messages.append({"role": "assistant", "content": thought or "OK."})
                messages.append({"role": "user", "content": nudge})
                continue
            # Guard (c): don't let it finish without telling the customer.
            if need_notify and not reminded_notify:
                reminded_notify = True
                nudge = ("You resolved/escalated the shipment but have not called "
                         "notify_customer yet. Make that function call now to inform the "
                         "customer, then finish.")
                messages.append({"role": "assistant", "content": thought or "OK."})
                messages.append({"role": "user", "content": nudge})
                continue
            entry = {"step": step, "thought": thought or "(final answer)",
                     "tool": None, "tool_input": None, "observation": "FINAL ANSWER",
                     "provider": last_provider}
            trace.append(entry)
            if on_step:
                on_step(entry)
            return finish(thought, step)

        # --- Execute the first requested tool call this turn. -------------
        call = tool_calls[0]
        name, args = call["name"], call.get("args", {})
        if isinstance(args, dict) and args.get("shipment_id"):
            active_shipment = args["shipment_id"]

        # Guard (a): short-circuit a state-changing call already known to fail hard.
        if name in STATE_CHANGING and (name, args.get("shipment_id")) in permanent_fail:
            observation = {"ok": False, "failure_class": "permanent",
                           "error": "This action already failed permanently. Do not retry -- "
                                    "choose a different action or escalate_to_human."}
        else:
            observation = dispatch(name, args)

        # Update guard state from the observation.
        if isinstance(observation, dict):
            if name in STATE_CHANGING and observation.get("ok") and not observation.get("idempotent_replay"):
                pending_verify = True
                need_notify = True
            if observation.get("failure_class") == "permanent":
                permanent_fail.add((name, args.get("shipment_id")))
            if name == "verify_shipment":
                pending_verify = False
            if name == "notify_customer" and observation.get("ok"):
                need_notify = False

        obs_short = _summarize(observation)
        entry = {"step": step, "thought": thought or f"I should use {name}.",
                 "tool": name, "tool_input": args, "observation": observation,
                 "provider": last_provider}
        trace.append(entry)
        if on_step:
            on_step(entry)

        # Feed the model its own move + the observation, then loop. NOTE: when the
        # model gave no thought text we echo a CONTENT-FREE filler (never a
        # "Called <tool>" string) so it can't learn to narrate tool calls as text
        # instead of emitting a real function call -- the tool-result turn already
        # records which tool ran.
        messages.append({"role": "assistant", "content": thought or "OK."})
        messages.append({"role": "tool", "name": name, "content": obs_short})

    # Ran out of iterations -- ask for a best-effort wrap-up.
    messages.append({"role": "user", "content": "You have hit the step limit. Give your best final answer now."})
    final = chat(messages)
    entry = {"step": MAX_ITERS + 1, "thought": "Reached max iterations; producing best-effort answer.",
             "tool": None, "tool_input": None, "observation": "FINAL ANSWER (forced)",
             "provider": final.get("provider", last_provider)}
    trace.append(entry)
    if on_step:
        on_step(entry)
    out = finish(final.get("text", ""), MAX_ITERS)
    return out


if __name__ == "__main__":
    import sys

    # Windows consoles default to cp1252; agent/LLM text may contain unicode.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    arg = " ".join(sys.argv[1:]).strip()
    # Shorthand: `python agent.py A` runs demo scenario A/B/C from its raw artifact.
    if arg.upper() in logistics.SCENARIOS:
        goal = logistics.scenario_goal(arg.upper())
    else:
        goal = arg or logistics.scenario_goal("A")

    out = run_agent(goal)
    print("\n=== TRACE ===")
    for s in out["trace"]:
        extra = ""
        obs = s.get("observation")
        if isinstance(obs, dict) and obs.get("attempts", 1) and obs.get("retry_log"):
            extra = f" [retries={len(obs['retry_log'])}]"
        print(f"[{s['step']}] ({s['provider']}) tool={s['tool']}{extra} :: {s['thought'][:70]}")
    print("\n=== ANSWER ===")
    print(out["answer"])
    if out.get("state_diff"):
        sd = out["state_diff"]
        print(f"\n=== STATE DIFF ({sd['shipment_id']}) resolved={sd['resolved']} ===")
        for d in sd["diff"]:
            print(f"  {d['field']}: {d['before']!r} -> {d['after']!r}")
