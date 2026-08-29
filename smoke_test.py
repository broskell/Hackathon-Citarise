"""Smoke test -- run: python smoke_test.py

Proves, WITHOUT any real outbound WhatsApp/email:
  1. chat() works via Gemini (or skips if no key).
  2. Groq failover triggers when Gemini is forced to fail.
  3. The logistics engine: indexes, snapshot/diff, transient-retry recovery,
     permanent fail-fast, idempotent replay.
  4. parse_exception's regex safety-net extracts the right ids/types.
  5. validate_address returns real (or gracefully-fallen-back) geocoding.
  6. notify_customer is SIMULATED while LIVE ACTIONS is off (no real send).

Prints PASS/FAIL/SKIP per check.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def line(name, ok, detail=""):
    # ok can be True (PASS), False (FAIL), or None (SKIP -- key not present).
    tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    print(f"[{tag}] {name}" + (f" -- {detail}" if detail else ""))
    return ok


def main():
    results = []
    print("=" * 64)
    print("SMOKE TEST -- Logistics Exception Agent")
    print("=" * 64)

    # --- 1) Gemini primary -------------------------------------------------
    import llm

    if llm.GEMINI_API_KEY:
        llm.FORCE_GEMINI_FAIL = False
        r = llm.chat([{"role": "user", "content": "Reply with the single word: OK"}])
        # A quota 429 on the free tier is expected under heavy use -> treat a clean
        # failover to Groq as acceptable here, not a hard failure.
        ok = r["provider"] in ("gemini", "groq") and not r["error"]
        results.append(line("chat() responds (Gemini or failover)", ok, f"provider={r['provider']}"))
    else:
        results.append(line("chat() via Gemini", None, "GEMINI_API_KEY missing"))

    # --- 2) Groq failover --------------------------------------------------
    if llm.GROQ_API_KEY:
        llm.FORCE_GEMINI_FAIL = True
        r = llm.chat([{"role": "user", "content": "Reply with the single word: OK"}])
        llm.FORCE_GEMINI_FAIL = False
        ok = r["provider"] == "groq" and not r["error"]
        results.append(line("Groq failover (forced Gemini error)", ok, f"provider={r['provider']}"))
    else:
        results.append(line("Groq failover", None, "GROQ_API_KEY missing"))

    print("-" * 64)
    print("LOGISTICS ENGINE (deterministic, no LLM, no real sends)")
    print("-" * 64)

    import logistics as L
    import tools as T

    # --- 3a) indexes + snapshot -------------------------------------------
    L.reset_db()
    ix_ok = (L.DB["_indexes"]["ship_by_order"].get("ORD-2002") == "SHP-2002"
             and "RapidEx" in L.DB["_indexes"]["carriers_by_region"].get("US-MW", [])
             and "SHP-1001" in L.DB["_snapshot"])
    results.append(line("reset_db builds indexes + snapshot", ix_ok))

    # --- 3b) diff after a mutation ----------------------------------------
    T.reroute_shipment("SHP-1001", "RapidEx")
    d = {x["field"]: (x["before"], x["after"]) for x in L.diff("SHP-1001")}
    diff_ok = d.get("carrier") == ("NorthWind", "RapidEx") and d.get("exception_code")[1] is None
    results.append(line("diff() reports field-level before/after", diff_ok, f"fields={list(d)}"))

    # --- 3c) transient retry recovers -------------------------------------
    L.reset_db()
    r = T.reschedule_delivery("SHP-2002", "2026-09-02")
    tr_ok = r.get("ok") and r.get("attempts") == 2 and len(r.get("retry_log", [])) == 1
    results.append(line("transient failure recovers on retry", tr_ok,
                        f"attempts={r.get('attempts')} retries={len(r.get('retry_log', []))}"))

    # --- 3d) permanent fails fast, no mutation ----------------------------
    L.reset_db()
    before_carrier = L.get_shipment_raw("SHP-3003")["carrier"]
    r = T.reassign_carrier("SHP-3003", "DesertHaul")
    after_carrier = L.get_shipment_raw("SHP-3003")["carrier"]
    perm_ok = (not r.get("ok") and r.get("failure_class") == "permanent"
               and r.get("attempts") == 1 and before_carrier == after_carrier)
    results.append(line("permanent failure fails fast (no mutation)", perm_ok,
                        f"class={r.get('failure_class')} attempts={r.get('attempts')}"))

    # --- 3e) idempotent replay --------------------------------------------
    L.reset_db()
    T.reroute_shipment("SHP-1001", "RapidEx")
    r2 = T.reroute_shipment("SHP-1001", "RapidEx")
    idem_ok = bool(r2.get("idempotent_replay"))
    results.append(line("idempotency: repeat call is a replay (no double write)", idem_ok))

    # --- 4) parse_exception regex safety-net ------------------------------
    a = T._regex_parse(L.RAW_EMAIL_A)
    b = T._regex_parse(L.RAW_WEBHOOK_B)
    parse_ok = (a["shipment_id"] == "SHP-1001" and a["exception_type"] == "WEATHER_HOLD"
                and b["shipment_id"] == "SHP-2002" and b["exception_type"] == "BAD_ADDRESS")
    results.append(line("parse_exception regex net extracts id + type", parse_ok,
                        f"A={a['shipment_id']}/{a['exception_type']} B={b['shipment_id']}/{b['exception_type']}"))

    # --- 5) validate_address (real Nominatim, or graceful fallback) --------
    r = T.validate_address("20 W 34 St, New York NY")
    addr_ok = r.get("ok") and r.get("source") in ("nominatim", "mock_fallback")
    results.append(line("validate_address returns geocode (real or fallback)", addr_ok,
                        f"source={r.get('source')}"))

    # --- 6) notify_customer gated (simulated while LIVE ACTIONS off) -------
    T.LIVE_ACTIONS = False
    L.reset_db()
    r = T.notify_customer("SHP-1001", "email", "test -- should NOT send")
    gate_ok = r.get("ok") and r.get("simulated") is True
    results.append(line("notify_customer simulated while LIVE ACTIONS off (no real send)", gate_ok))

    print("=" * 64)
    passed = sum(1 for r in results if r is True)
    failed = sum(1 for r in results if r is False)
    skipped = sum(1 for r in results if r is None)
    print(f"SUMMARY: {passed} passed, {failed} failed, {skipped} skipped (missing keys)")
    print("=" * 64)


if __name__ == "__main__":
    main()
