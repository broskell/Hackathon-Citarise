"""Mock logistics system + resilient-execution engine for the Logistics Exception Agent.

This module is deliberately LLM-free and side-effect-free (no network, no disk).
Everything the agent's tools mutate lives here, which makes the whole thing
deterministic and unit-testable without an API key.

What lives here
---------------
* SEED / DB          -- the in-memory "database" (shipments, orders, customers,
                        carriers, inventory) plus the raw exception artifacts.
* reset_db()         -- reload SEED, rebuild indexes, snapshot every shipment.
                        Called once at the start of every agent run so a run's
                        before->after lifecycle is self-contained and repeatable.
* Indexes            -- auxiliary dicts for O(1) lookups instead of list scans.
* Snapshot/diff      -- capture pre-agent state, then diff live state against it.
                        This is what powers the UI's before/after panel.
* Resilient exec     -- execute_state_change() gives EVERY mutation:
                          - idempotency (same key -> cached result, no double write)
                          - bounded exponential-backoff retry
                          - a code-level transient (retry) vs permanent (escalate)
                            failure distinction, driven by per-shipment injection.

The agent's tool wrappers (schemas + argument handling) live in tools.py and call
into the helpers exposed here.
"""

import copy
import time

# Orders at or above this value are too important to auto-resolve on a hard
# failure -- policy escalates them to a human instead.
ESCALATE_THRESHOLD = 1000


# ==========================================================================
# Typed failures -- the retry layer treats these two very differently.
# ==========================================================================
class TransientFailure(Exception):
    """A temporary error (timeout, 503). Worth retrying with backoff."""


class PermanentFailure(Exception):
    """A hard error (carrier offline, no capacity). Retrying is pointless;
    fail fast so the agent escalates."""


# ==========================================================================
# Raw exception artifacts -- the messy, real-world input each scenario starts
# from. The agent's FIRST job is to extract a shipment id + exception type out
# of this noise before it can do anything.
# ==========================================================================
RAW_EMAIL_A = """From: notifications@northwind-logistics.com
To: ops@ourstore.example
Subject: [AUTOMATED] Delivery Exception Notice - Action May Be Required
Date: Fri, 29 Aug 2026 08:14:22 -0500
X-Mailer: NorthWind AutoNotify v3.2
Message-ID: <9f8a4c21-77de-4b0a-9c2e-1a2b3c4d5e6f@northwind>
X-Priority: 3 (Normal)
Content-Type: text/plain; charset="utf-8"

Dear Valued Shipping Partner,

This is an automated notification regarding a service disruption affecting our
Midwest ground network. Due to severe winter weather (blizzard conditions and
a ground stop) at our Chicago sortation hub, all outbound processing has been
temporarily suspended as of this morning.

One or more of your active shipments may be impacted. The reference associated
with THIS notice is SHP-1001 (customer order ORD-1001), which is currently
being held at HUB-CHI pending weather clearance. Estimated additional transit
delay at this time: 48+ hours, subject to change.

No action is required on your part unless you wish to expedite delivery via an
alternate carrier. Standard service will resume automatically once conditions
at the affected facility improve.

Regards,
NorthWind Logistics -- Automated Operations Center
--- This is an unmonitored mailbox. Please do not reply to this message. ---
"""

# Semi-structured webhook: inconsistent keys, nulls, extra vendor cruft, and a
# usable-but-messy updated address that we'll geocode for real.
RAW_WEBHOOK_B = """{
  "event": "delivery.failed",
  "v": 2,
  "trackingRef": "SHP-2002",
  "attempt_count": 2,
  "reason_code": "ADDRESS_NOT_FOUND",
  "reason": "Courier could not locate the delivery address after 2 attempts",
  "addr1": "742 Evergreen Terrace",
  "address_line": null,
  "updated_address": "20 W 34 St, New York NY",
  "customer_ref": "CUST-2002",
  "carrier": "RapidEx",
  "_meta": { "webhook_id": "wh_abc123", "source": "carrier-push", "retryable": false },
  "notes": ""
}"""

# Scenario C's raw input is a PHOTO (see make_assets.py); this is the fallback
# text used only if the image path is unavailable.
RAW_PHOTO_TEXT_C = (
    "HANDWRITTEN COURIER SLIP (damaged label): "
    "TRK SHP-3003 / ORD-3003 -- 'SwiftCargo system DOWN, cannot scan or move "
    "package, returning to LAX hub' -- driver #4471"
)
PHOTO_PATH_C = "assets/label_SHP-3003.png"


# ==========================================================================
# SEED -- the authoritative starting state, reloaded on every reset_db().
# ==========================================================================
def _seed():
    return {
        "shipments": {
            # A: weather hold, resolvable by rerouting to an up carrier.
            "SHP-1001": {
                "id": "SHP-1001", "order_id": "ORD-1001", "status": "in_transit",
                "carrier": "NorthWind", "current_hub": "HUB-CHI",
                "destination": "123 Michigan Ave, Chicago, IL 60601", "region": "US-MW",
                "eta": "2026-08-30", "exception_code": "WEATHER_HOLD",
                "exception_note": "Blizzard / ground stop at HUB-CHI, outbound halted",
                "attempts": 0,
                "_fail_tool": None, "_fail_mode": None, "_fail_after": 0,
            },
            # B: bad address + 2 failed attempts. reschedule_delivery is flaky
            # (transient) and recovers on retry.
            "SHP-2002": {
                "id": "SHP-2002", "order_id": "ORD-2002", "status": "delivery_failed",
                "carrier": "RapidEx", "current_hub": "HUB-NYC",
                "destination": "742 Evergreen Terrace, Sprngfield", "region": "US-NE",
                "eta": "2026-08-29", "exception_code": "BAD_ADDRESS",
                "exception_note": "Courier could not locate address after 2 attempts",
                "attempts": 2,
                "_fail_tool": "reschedule_delivery", "_fail_mode": "transient", "_fail_after": 1,
            },
            # C: carrier outage on a high-value order with no alternate carrier
            # in-region. reassign_carrier fails permanently -> escalate.
            "SHP-3003": {
                "id": "SHP-3003", "order_id": "ORD-3003", "status": "delayed",
                "carrier": "SwiftCargo", "current_hub": "HUB-LAX",
                "destination": "500 S Grand Ave, Los Angeles, CA 90071", "region": "US-SW",
                "eta": "2026-08-31", "exception_code": "CARRIER_OUTAGE",
                "exception_note": "SwiftCargo API outage; package stranded at HUB-LAX",
                "attempts": 0,
                "_fail_tool": "reassign_carrier", "_fail_mode": "permanent", "_fail_after": 0,
            },
        },
        "orders": {
            "ORD-1001": {"id": "ORD-1001", "customer_id": "CUST-1001", "value_usd": 240,
                         "items": ["SKU-BOOK-1"], "priority": "standard", "credit_usd": 0},
            "ORD-2002": {"id": "ORD-2002", "customer_id": "CUST-2002", "value_usd": 89,
                         "items": ["SKU-MUG-2"], "priority": "standard", "credit_usd": 0},
            "ORD-3003": {"id": "ORD-3003", "customer_id": "CUST-3003", "value_usd": 1850,
                         "items": ["SKU-TV-3"], "priority": "high", "credit_usd": 0},
        },
        "customers": {
            "CUST-1001": {"id": "CUST-1001", "name": "Alice Nguyen",
                          "phone": "+14155550101", "email": "alice@example.com",
                          "address": "123 Michigan Ave, Chicago, IL 60601",
                          "preferred_channel": "email"},
            "CUST-2002": {"id": "CUST-2002", "name": "Bob Ramirez",
                          "phone": "+14155550102", "email": "bob@example.com",
                          "address": "20 W 34 St, New York NY",
                          "preferred_channel": "whatsapp"},
            "CUST-3003": {"id": "CUST-3003", "name": "Carol Smith",
                          "phone": "+14155550103", "email": "carol@example.com",
                          "address": "500 S Grand Ave, Los Angeles, CA 90071",
                          "preferred_channel": "email"},
        },
        "carriers": {
            "NorthWind": {"id": "NorthWind", "name": "NorthWind Logistics",
                          "regions": ["US-MW"], "capacity_left": 4,
                          "api_status": "up", "avg_delay_hrs": 48},
            "RapidEx":   {"id": "RapidEx", "name": "RapidEx Courier",
                          "regions": ["US-MW", "US-NE"], "capacity_left": 12,
                          "api_status": "up", "avg_delay_hrs": 6},
            "SwiftCargo": {"id": "SwiftCargo", "name": "SwiftCargo Freight",
                           "regions": ["US-SW"], "capacity_left": 3,
                           "api_status": "down", "avg_delay_hrs": 0},
            # Looks healthy, so the agent will attempt to reassign onto it -- but the
            # reassignment itself fails PERMANENTLY (see SHP-3003 injection), which is
            # what forces the escalate decision. Exercises the permanent-failure path.
            "DesertHaul": {"id": "DesertHaul", "name": "DesertHaul Freight",
                           "regions": ["US-SW"], "capacity_left": 6,
                           "api_status": "up", "avg_delay_hrs": 12},
        },
        "inventory": {
            "SKU-BOOK-1@HUB-CHI": {"sku": "SKU-BOOK-1", "warehouse": "HUB-CHI", "qty": 30},
            "SKU-MUG-2@HUB-NYC":  {"sku": "SKU-MUG-2", "warehouse": "HUB-NYC", "qty": 8},
            "SKU-TV-3@HUB-LAX":   {"sku": "SKU-TV-3", "warehouse": "HUB-LAX", "qty": 2},
        },
    }


# Which raw artifact + target shipment each demo scenario starts from.
SCENARIOS = {
    "A": {"label": "Weather hold (raw carrier email)", "shipment": "SHP-1001",
          "source_type": "email", "artifact": RAW_EMAIL_A},
    "B": {"label": "Failed delivery / bad address (JSON webhook)", "shipment": "SHP-2002",
          "source_type": "webhook_json", "artifact": RAW_WEBHOOK_B},
    "C": {"label": "Carrier outage (photo of damaged label)", "shipment": "SHP-3003",
          "source_type": "ocr_text", "artifact": PHOTO_PATH_C},
}


def custom_goal(raw_input, source_type="email"):
    """Build the agent goal from a user-supplied raw artifact (custom input).
    Same instruction as the built-in scenarios, but with the operator's own text."""
    return (
        "A logistics exception has been reported.\n\n"
        f"Raw artifact (source_type={source_type}):\n\n{raw_input}\n\n"
        "Handle it end-to-end: extract the shipment, diagnose, resolve it or escalate, "
        "verify the outcome with a before/after check, and notify the customer."
    )


def scenario_goal(key):
    """Build the agent goal string for demo scenario A/B/C, embedding its raw
    artifact. For the photo scenario the artifact is a file path the agent must
    OCR with extract_from_document first."""
    sc = SCENARIOS[key]
    if sc["source_type"] == "ocr_text":
        artifact = (f"The raw artifact is a PHOTO of a damaged shipping label at this "
                    f"path: {sc['artifact']}\nOCR it with extract_from_document, then "
                    f"parse the extracted text.")
    else:
        artifact = f"Raw artifact (source_type={sc['source_type']}):\n\n{sc['artifact']}"
    return (
        "A logistics exception has been reported.\n\n"
        f"{artifact}\n\n"
        "Handle it end-to-end: extract the shipment, diagnose, resolve it or escalate, "
        "verify the outcome with a before/after check, and notify the customer."
    )


# ==========================================================================
# DB + lifecycle
# ==========================================================================
DB = {}          # populated by reset_db()
_seq_counter = 0  # monotonic; replaces wall-clock so the trace is deterministic


def _next_seq():
    global _seq_counter
    _seq_counter += 1
    return _seq_counter


def reset_db():
    """Reload SEED, rebuild indexes, and snapshot every shipment (+ its order and
    customer) as the authoritative 'before' state. Call once per agent run."""
    global DB, _seq_counter
    _seq_counter = 0
    data = _seed()
    DB = {
        **data,
        "audit_log": [],
        "_idempotency": {},
        "_snapshot": {},
        "_indexes": {},
    }
    _build_indexes()
    # Snapshot each shipment together with the order/customer it points at.
    for sid, sh in DB["shipments"].items():
        order = DB["orders"].get(sh["order_id"], {})
        cust = DB["customers"].get(order.get("customer_id"), {})
        DB["_snapshot"][sid] = {
            "shipment": copy.deepcopy(sh),
            "order": copy.deepcopy(order),
            "customer": copy.deepcopy(cust),
        }
    return DB


def _build_indexes():
    """Secondary indexes for O(1) lookups instead of scanning the tables."""
    ix = {
        "ship_by_order": {},     # order_id  -> shipment_id
        "orders_by_customer": {},  # customer_id -> [order_id, ...]
        "carriers_by_region": {},  # region -> [carrier_id, ...]
        "ship_by_tracking": {},  # tracking/shipment id -> shipment_id (identity today,
                                 # but the indirection lets tracking != id later)
    }
    for sid, sh in DB["shipments"].items():
        ix["ship_by_order"][sh["order_id"]] = sid
        ix["ship_by_tracking"][sid] = sid
    for oid, o in DB["orders"].items():
        ix["orders_by_customer"].setdefault(o["customer_id"], []).append(oid)
    for cid, c in DB["carriers"].items():
        for region in c.get("regions", []):
            ix["carriers_by_region"].setdefault(region, []).append(cid)
    DB["_indexes"] = ix


# --- read accessors (index-backed) ----------------------------------------
def get_shipment_raw(shipment_id):
    return DB["shipments"].get(shipment_id)


def get_order_raw(order_id):
    return DB["orders"].get(order_id)


def get_customer_for_order(order_id):
    o = DB["orders"].get(order_id, {})
    return DB["customers"].get(o.get("customer_id"))


def carriers_in_region(region):
    ids = DB["_indexes"]["carriers_by_region"].get(region, [])
    return [DB["carriers"][cid] for cid in ids]


def inventory_for(sku, warehouse):
    return DB["inventory"].get(f"{sku}@{warehouse}")


# ==========================================================================
# Resilient execution: idempotency + retry + transient/permanent injection
# ==========================================================================
def _maybe_inject_failure(shipment, tool_name, attempt):
    """Raise a typed failure if this shipment is rigged to fail this tool.

    * permanent -> raise every time (fail fast, no retry).
    * transient -> raise while attempt <= _fail_after, then succeed.
    """
    if shipment.get("_fail_tool") != tool_name:
        return
    mode = shipment.get("_fail_mode")
    if mode == "permanent":
        raise PermanentFailure(
            f"{tool_name}: carrier/API permanently unavailable for {shipment['id']}"
        )
    if mode == "transient" and attempt <= shipment.get("_fail_after", 0):
        raise TransientFailure(
            f"{tool_name}: transient error (attempt {attempt}) for {shipment['id']}"
        )


def with_retry(attempt_fn, max_attempts=3, base_delay=0.2):
    """Run attempt_fn(attempt) with bounded exponential backoff.

    Retries only TransientFailure; PermanentFailure fails fast. Returns a dict:
      ok True  -> {"ok": True,  "attempts": n, "retry_log": [...], "value": <fn result>}
      ok False -> {"ok": False, "attempts": n, "retry_log": [...],
                   "failure_class": "permanent"|"transient_exhausted", "error": str}
    """
    retry_log = []
    for attempt in range(1, max_attempts + 1):
        try:
            value = attempt_fn(attempt)
            return {"ok": True, "attempts": attempt, "retry_log": retry_log, "value": value}
        except PermanentFailure as e:
            return {"ok": False, "attempts": attempt, "retry_log": retry_log,
                    "failure_class": "permanent", "error": str(e)}
        except TransientFailure as e:
            delay = round(base_delay * (2 ** (attempt - 1)), 3)
            retry_log.append({"attempt": attempt, "delay_s": delay, "error": str(e)})
            if attempt < max_attempts:
                time.sleep(delay)
            else:
                return {"ok": False, "attempts": attempt, "retry_log": retry_log,
                        "failure_class": "transient_exhausted", "error": str(e)}


def execute_state_change(tool_name, shipment_id, mutate_fn, idem_key):
    """The single choke point every state-changing tool goes through.

    Args:
        tool_name:  name of the calling tool (used for failure injection + audit).
        shipment_id: target shipment.
        mutate_fn:  zero-arg callable that applies the change to DB and returns a
                    dict of {field: new_value} describing what it changed. It is
                    only called on a non-failing attempt.
        idem_key:   canonical key; a repeat call with the same key returns the
                    cached result without mutating anything again.
    """
    sh = get_shipment_raw(shipment_id)
    if not sh:
        return {"ok": False, "error": f"unknown shipment {shipment_id}"}

    # Idempotency: same logical action -> same result, no second write.
    if idem_key in DB["_idempotency"]:
        cached = dict(DB["_idempotency"][idem_key])
        cached["idempotent_replay"] = True
        return cached

    def attempt_fn(attempt):
        _maybe_inject_failure(sh, tool_name, attempt)
        return mutate_fn()

    r = with_retry(attempt_fn)
    seq = _next_seq()

    if r["ok"]:
        result = {"ok": True, "shipment_id": shipment_id,
                  "attempts": r["attempts"], "changed": r["value"]}
    else:
        result = {"ok": False, "shipment_id": shipment_id,
                  "attempts": r["attempts"], "failure_class": r["failure_class"],
                  "error": r["error"]}
    if r["retry_log"]:
        result["retry_log"] = r["retry_log"]

    DB["audit_log"].append({
        "seq": seq, "tool": tool_name, "shipment_id": shipment_id,
        "ok": result["ok"], "attempts": result["attempts"],
        "failure_class": result.get("failure_class"),
        "changed": result.get("changed"),
        "retry_log": result.get("retry_log"),
    })
    DB["_idempotency"][idem_key] = result
    return result


# ==========================================================================
# Verify + diff -- the core differentiator surfaced to the UI.
# ==========================================================================
# Internal bookkeeping fields never shown in a diff.
_HIDDEN = {"_fail_tool", "_fail_mode", "_fail_after", "region"}


def diff(shipment_id):
    """Field-level diff of the shipment now vs. the pre-agent snapshot."""
    snap = DB["_snapshot"].get(shipment_id, {}).get("shipment", {})
    cur = DB["shipments"].get(shipment_id, {})
    out = []
    for field in sorted(set(snap) | set(cur)):
        if field in _HIDDEN:
            continue
        before, after = snap.get(field), cur.get(field)
        if before != after:
            out.append({"field": field, "before": before, "after": after})
    # Surface a goodwill credit as an order-level diff row too.
    snap_order = DB["_snapshot"].get(shipment_id, {}).get("order", {})
    cur_order = DB["orders"].get(cur.get("order_id"), {})
    if snap_order.get("credit_usd") != cur_order.get("credit_usd"):
        out.append({"field": "order.credit_usd",
                    "before": snap_order.get("credit_usd"),
                    "after": cur_order.get("credit_usd")})
    return out


def is_resolved(shipment_id):
    """A shipment counts as resolved when its exception is cleared OR it has been
    explicitly handed to a human (escalation is a valid terminal outcome)."""
    cur = DB["shipments"].get(shipment_id, {})
    code = cur.get("exception_code")
    if code in (None, "", "RESOLVED"):
        return True
    if code == "ESCALATED":
        return True  # routed to a human on purpose
    return False


def final_report(shipment_id):
    """Everything the UI's persistent State Diff panel needs."""
    return {
        "shipment_id": shipment_id,
        "diff": diff(shipment_id),
        "resolved": is_resolved(shipment_id),
        "current_state": DB["shipments"].get(shipment_id),
        "audit_log": DB["audit_log"],
    }
