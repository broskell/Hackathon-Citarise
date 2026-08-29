"""Deterministic offline replay for when BOTH LLM providers are rate-limited.

This is a demo-resilience fallback, in the same spirit as the Nominatim and
vision cache fallbacks: it streams a GENUINELY REAL run — every tool executes
for real against the logistics engine (real state diffs, real exponential-backoff
retry, real permanent-failure detection, real OpenStreetMap geocoding, real
vision cache fallback). The ONLY thing scripted is the sequence of tool choices,
which mirrors exactly what the LLM selected in the proven live runs.

The server auto-switches to this when a health probe shows no LLM is available,
and hands back to the live LLM planner the moment quota returns.
"""

import time

import logistics
from tools import dispatch

PROVIDER = "replay"

# Canonical tool sequence per scenario: (thought, tool, args). These mirror the
# LLM's proven choices; the observations are produced by the real tools.
SCRIPTS = {
    "A": [
        ("Parsing the raw carrier email to recover the shipment id + exception.",
         "parse_exception", {"raw_input": logistics.RAW_EMAIL_A, "source_type": "email"}),
        ("Loading authoritative shipment state to diagnose.",
         "get_shipment", {"shipment_id": "SHP-1001"}),
        ("Weather hold — looking for a healthy alternate carrier in-region.",
         "list_carriers", {"region": "US-MW"}),
        ("RapidEx is up with capacity; rerouting to clear the hold.",
         "reroute_shipment", {"shipment_id": "SHP-1001", "new_carrier": "RapidEx"}),
        ("Verifying the change against authoritative state.",
         "verify_shipment", {"shipment_id": "SHP-1001"}),
        ("Notifying the customer of the reroute + new ETA.",
         "notify_customer", {"shipment_id": "SHP-1001", "channel": "email",
                             "message": "Your shipment SHP-1001 was rerouted to RapidEx due to a "
                                        "weather hold and is now expected by 2026-09-01."}),
    ],
    "B": [
        ("Parsing the JSON webhook (messy fields) for id + exception.",
         "parse_exception", {"raw_input": logistics.RAW_WEBHOOK_B, "source_type": "webhook_json"}),
        ("Loading shipment state — bad address, two failed attempts.",
         "get_shipment", {"shipment_id": "SHP-2002"}),
        ("Validating the corrected address against real geocoding.",
         "validate_address", {"address": "20 W 34 St, New York NY"}),
        ("Writing the normalized address to the shipment.",
         "update_address", {"shipment_id": "SHP-2002",
                            "address": "20 W 34th St, New York, NY 10001, USA"}),
        ("Rescheduling delivery (booking may hit a transient error and retry).",
         "reschedule_delivery", {"shipment_id": "SHP-2002", "new_eta": "2026-09-02"}),
        ("Goodwill credit for the two failed attempts.",
         "issue_credit", {"order_id": "ORD-2002", "amount": 5, "reason": "failed_delivery"}),
        ("Verifying the resolution against authoritative state.",
         "verify_shipment", {"shipment_id": "SHP-2002"}),
        ("Notifying the customer on their preferred channel.",
         "notify_customer", {"shipment_id": "SHP-2002", "channel": "whatsapp",
                             "message": "Good news — we corrected your address and rescheduled "
                                        "delivery for 2026-09-02, plus a $5 credit for the trouble."}),
    ],
    "C": [
        ("The artifact is a photo — OCR the damaged label with vision.",
         "extract_from_document", {"file_path": logistics.PHOTO_PATH_C,
                                   "prompt": "Extract all text, especially the tracking number."}),
        ("Parsing the OCR text for the shipment id + exception.",
         "parse_exception", {"raw_input": logistics.RAW_PHOTO_TEXT_C, "source_type": "ocr_text"}),
        ("Loading state — carrier outage on a high-value order.",
         "get_shipment", {"shipment_id": "SHP-3003"}),
        ("Checking for a healthy alternate carrier in-region.",
         "list_carriers", {"region": "US-SW"}),
        ("Attempting to reassign onto DesertHaul.",
         "reassign_carrier", {"shipment_id": "SHP-3003", "carrier": "DesertHaul"}),
        ("Reassignment failed permanently + high-value order — escalating to a human.",
         "escalate_to_human", {"shipment_id": "SHP-3003",
                               "reason": "Carrier outage; reassignment failed permanently on a "
                                         "high-value order.", "priority": "high"}),
        ("Verifying the escalation is recorded in authoritative state.",
         "verify_shipment", {"shipment_id": "SHP-3003"}),
        ("Notifying the customer that a specialist is handling it.",
         "notify_customer", {"shipment_id": "SHP-3003", "channel": "email",
                             "message": "Your shipment SHP-3003 hit a carrier outage; a specialist "
                                        "is now handling it directly and will be in touch."}),
    ],
}

_ANSWER = {
    "A": "**Weather hold, resolved.** SHP-1001 was held at the Chicago hub by a blizzard. "
         "I rerouted it from NorthWind to **RapidEx** (a healthy in-region carrier), which cleared "
         "the hold and set a new ETA of 2026-09-01, verified the change, and notified the customer.",
    "B": "**Failed delivery, resolved.** SHP-2002 failed twice on a bad address. I validated the "
         "corrected address against **live geocoding**, updated it, **rescheduled** delivery "
         "(the booking hit a transient error and recovered on retry), issued a goodwill credit, "
         "verified the fix, and notified the customer.",
    "C": "**Carrier outage, escalated.** SHP-3003 was stranded by a SwiftCargo outage on a "
         "high-value order. I attempted to reassign to DesertHaul; the reassignment **failed "
         "permanently**, so — rather than retry uselessly — I **escalated to a human**, verified "
         "the escalation, and notified the customer.",
}


def run_replay(scenario: str, on_step=None, step_delay: float = 0.45) -> dict:
    """Execute a scenario's canonical tool sequence against the REAL tools,
    streaming each step via on_step. Returns the same shape as run_agent()."""
    logistics.reset_db()
    script = SCRIPTS[scenario]
    trace = []
    active = None
    for i, (thought, tool, args) in enumerate(script, start=1):
        if args.get("shipment_id"):
            active = args["shipment_id"]
        observation = dispatch(tool, args)
        entry = {"step": i, "thought": thought, "tool": tool, "tool_input": args,
                 "observation": observation, "provider": PROVIDER}
        trace.append(entry)
        if on_step:
            on_step(entry)
        time.sleep(step_delay)

    final = {"step": len(script) + 1, "thought": "Done.", "tool": None,
             "tool_input": None, "observation": "FINAL ANSWER", "provider": PROVIDER}
    trace.append(final)
    if on_step:
        on_step(final)

    return {"answer": _ANSWER[scenario], "trace": trace, "iterations": len(script),
            "state_diff": logistics.final_report(active) if active else None}
