"""Agent tools. Each is a plain function that returns a short structured dict
and NEVER raises -- failures come back as {"ok": False, "error": "..."}.

Every tool carries a JSON schema; the TOOLS registry at the bottom is what you
hand to llm.chat(..., tools=TOOLS). To run a tool by name, use dispatch().

Graceful degradation: if a provider's key is missing, the tool prints a warning
once and returns {"ok": False, "error": "<KEY> not set"} instead of crashing.
"""

import json
import os
import re

from dotenv import load_dotenv

import logistics as L

load_dotenv()

# --- LIVE ACTIONS gate --------------------------------------------------
# When False (default), notify_customer SIMULATES sends instead of hitting
# Twilio/Resend. The Streamlit sidebar toggle flips tools.LIVE_ACTIONS before a
# run; the env var lets headless runs opt in too. This is the ONLY gate on real
# outbound side effects -- every DB mutation is local and always safe.
LIVE_ACTIONS = os.getenv("LIVE_ACTIONS", "0") == "1"

# --- Human-oversight approval gate --------------------------------------
# High-consequence FINANCIAL actions at/above this limit require human approval.
# Demo mode surfaces them as require_approval and auto-approves so the flow
# completes; set REQUIRE_APPROVAL=1 to make them BLOCK (status
# "awaiting_human_approval") until a person signs off, mutating nothing. Together
# with the LIVE ACTIONS gate on real customer sends and escalate_to_human, this
# is the human-oversight layer.
APPROVAL_THRESHOLD_USD = float(os.getenv("APPROVAL_THRESHOLD_USD", "100"))
REQUIRE_APPROVAL = os.getenv("REQUIRE_APPROVAL", "0") == "1"

# --- keys ---
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM = os.getenv("RESEND_FROM", "onboarding@resend.dev").strip()
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_warned = set()


def _warn(msg):
    if msg not in _warned:
        print(f"[tools][WARN] {msg}", flush=True)
        _warned.add(msg)


# ==========================================================================
# 1) web_search -- Tavily
# ==========================================================================
def web_search(query: str, max_results: int = 5) -> dict:
    """Search the live web with Tavily and return a short list of results.

    Args:
        query: The search query.
        max_results: How many results to return (default 5).
    """
    if not TAVILY_API_KEY:
        _warn("TAVILY_API_KEY not set -- web_search disabled")
        return {"ok": False, "error": "TAVILY_API_KEY not set", "results": []}
    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=TAVILY_API_KEY)
        resp = client.search(query=query, max_results=max_results, include_answer=True)
        results = [
            {"title": r.get("title"), "url": r.get("url"), "content": (r.get("content") or "")[:500]}
            for r in resp.get("results", [])
        ]
        return {"ok": True, "answer": resp.get("answer"), "results": results}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "results": []}


# ==========================================================================
# 2) send_whatsapp -- Twilio
# ==========================================================================
def send_whatsapp(to: str, body: str) -> dict:
    """Send a WhatsApp message via Twilio.

    NOTE: On a Twilio trial/sandbox, `to` MUST be a number that has already
    joined your WhatsApp sandbox (a verified recipient). Pass a bare number
    like "+1415...": the "whatsapp:" prefix is added automatically.

    Args:
        to: Destination phone number in E.164 form, e.g. "+14155551234".
        body: The message text.
    """
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
        _warn("TWILIO creds not set -- send_whatsapp disabled")
        return {"ok": False, "error": "TWILIO_ACCOUNT_SID/AUTH_TOKEN not set"}
    if not TWILIO_WHATSAPP_FROM:
        _warn("TWILIO_WHATSAPP_FROM not set -- using sandbox default whatsapp:+14155238886")
    sender = TWILIO_WHATSAPP_FROM or "whatsapp:+14155238886"
    if not sender.startswith("whatsapp:"):
        sender = "whatsapp:" + sender
    dest = to if to.startswith("whatsapp:") else "whatsapp:" + to
    try:
        from twilio.rest import Client

        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        msg = client.messages.create(from_=sender, to=dest, body=body)
        return {"ok": True, "sid": msg.sid, "status": msg.status}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ==========================================================================
# 3) send_email -- Resend
# ==========================================================================
def send_email(to: str, subject: str, body: str) -> dict:
    """Send an email via Resend.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body (plain text; sent as a simple HTML paragraph).
    """
    if not RESEND_API_KEY:
        _warn("RESEND_API_KEY not set -- send_email disabled")
        return {"ok": False, "error": "RESEND_API_KEY not set"}
    try:
        import resend

        resend.api_key = RESEND_API_KEY
        html = "<p>" + body.replace("\n", "<br>") + "</p>"
        r = resend.Emails.send(
            {"from": RESEND_FROM, "to": [to], "subject": subject, "html": html}
        )
        return {"ok": True, "id": r.get("id") if isinstance(r, dict) else str(r)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ==========================================================================
# 4) extract_from_document -- Gemini multimodal (images + PDFs)
# ==========================================================================
def extract_from_document(file_path: str, prompt: str = "") -> dict:
    """Extract text / structured info from a local image or PDF using Gemini vision.

    Resilience: a successful read is cached to a "<file>.ocr.txt" sidecar. If a
    later call fails (Gemini has no Groq-style vision failover, and the free tier
    is rate-limited), we fall back to that cache so the demo never dies on a
    transient 429 -- the same live-first / graceful-fallback pattern as
    validate_address. The `source` field says which path served the result.

    Args:
        file_path: Path to a local .png/.jpg/.pdf (or similar) file.
        prompt: Optional instruction, e.g. "pull out the invoice total and date".
    """
    if not os.path.exists(file_path):
        return {"ok": False, "error": f"file not found: {file_path}", "text": ""}
    cache_path = file_path + ".ocr.txt"

    def _cache_fallback(err):
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return {"ok": True, "source": "cache_fallback", "text": f.read().strip(),
                            "note": f"live vision unavailable ({err}); used cached OCR"}
            except Exception:
                pass
        return {"ok": False, "error": err, "text": ""}

    if not GEMINI_API_KEY:
        _warn("GEMINI_API_KEY not set -- extract_from_document disabled")
        return _cache_fallback("GEMINI_API_KEY not set")
    try:
        import mimetypes

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        with open(file_path, "rb") as f:
            data = f.read()
        instruction = prompt or "Extract all text and key information from this document."
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Part.from_bytes(data=data, mime_type=mime), instruction],
        )
        text = (resp.text or "").strip()
        try:  # refresh the cache on every live success
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass
        return {"ok": True, "source": "gemini_vision", "text": text}
    except Exception as e:
        return _cache_fallback(f"{type(e).__name__}: {e}")


# ==========================================================================
# 5) rag_search + rag_add -- in-memory ChromaDB with Gemini embeddings
# ==========================================================================
_collection = None
_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")


def _embed(texts):
    """Embed a list of strings with Gemini; returns list[list[float]] or None on failure."""
    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)
    resp = client.models.embed_content(model=_EMBED_MODEL, contents=texts)
    return [e.values for e in resp.embeddings]


def _get_collection():
    global _collection
    if _collection is None:
        import chromadb

        # Ephemeral in-memory client -- perfect for a demo, nothing to clean up.
        client = chromadb.EphemeralClient()
        _collection = client.get_or_create_collection("rag")
    return _collection


def rag_add(texts) -> dict:
    """Load documents into the in-memory RAG store.

    Args:
        texts: A string or list of strings to index.
    """
    if not GEMINI_API_KEY:
        _warn("GEMINI_API_KEY not set -- rag_add disabled")
        return {"ok": False, "error": "GEMINI_API_KEY not set", "added": 0}
    if isinstance(texts, str):
        texts = [texts]
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return {"ok": False, "error": "no non-empty texts", "added": 0}
    try:
        col = _get_collection()
        base = col.count()
        embeddings = _embed(texts)
        ids = [f"doc-{base + i}" for i in range(len(texts))]
        col.add(ids=ids, documents=texts, embeddings=embeddings)
        return {"ok": True, "added": len(texts), "total": col.count()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "added": 0}


def rag_search(query: str, k: int = 3) -> dict:
    """Semantic search over documents previously loaded with rag_add.

    Args:
        query: The question / search text.
        k: Number of chunks to return (default 3).
    """
    if not GEMINI_API_KEY:
        _warn("GEMINI_API_KEY not set -- rag_search disabled")
        return {"ok": False, "error": "GEMINI_API_KEY not set", "matches": []}
    try:
        col = _get_collection()
        if col.count() == 0:
            return {"ok": True, "matches": [], "note": "RAG store is empty -- call rag_add first"}
        q_emb = _embed([query])
        res = col.query(query_embeddings=q_emb, n_results=min(k, col.count()))
        docs = (res.get("documents") or [[]])[0]
        return {"ok": True, "matches": docs}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "matches": []}


# ==========================================================================
# LOGISTICS EXCEPTION AGENT TOOLS
# --------------------------------------------------------------------------
# All of these read/write the in-memory system in logistics.py. State-changing
# tools go through L.execute_state_change (idempotency + retry + failure class);
# reads are index-backed. None of them raise -- failures come back as dicts.
# ==========================================================================

_EXCEPTION_KEYWORDS = [
    ("WEATHER_HOLD", ("weather", "blizzard", "storm", "snow", "ground stop", "hold")),
    ("BAD_ADDRESS", ("address", "not found", "not located", "undeliverable", "wrong address")),
    ("CARRIER_OUTAGE", ("outage", "api down", "system down", "unavailable", "cannot scan", "offline")),
]


def _extract_json(text: str):
    """Pull the first {...} object out of an LLM reply. Returns dict or None."""
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None


def _regex_parse(raw_input: str) -> dict:
    """Deterministic safety-net extractor: shipment id + exception type."""
    sid = None
    m = re.search(r"SHP-\d+", raw_input or "", re.IGNORECASE)
    if m:
        sid = m.group(0).upper()
    low = (raw_input or "").lower()
    etype = "OTHER"
    for code, words in _EXCEPTION_KEYWORDS:
        if any(w in low for w in words):
            etype = code
            break
    return {"shipment_id": sid, "exception_type": etype,
            "extracted_fields": {}, "confidence": 0.4 if sid else 0.0,
            "method": "regex"}


# ==========================================================================
# parse_exception -- entity extraction from raw, messy input (LLM + regex net)
# ==========================================================================
def parse_exception(raw_input: str, source_type: str = "email") -> dict:
    """Extract the shipment id + exception type from a raw exception artifact.

    Handles unstructured email prose, semi-structured JSON webhooks, and OCR text
    off a photo.

    EFFICIENCY: deterministic-first. The cheap regex/keyword extractor runs first;
    the LLM is invoked ONLY when the fixed rules are uncertain (no shipment id, or
    an unrecognized exception type). This is precisely "AI where fixed rules fail"
    -- it demonstrates AI necessity on ambiguous input while saving a full LLM call
    on clean input (typically one fewer model call per run).

    Args:
        raw_input: The raw artifact text (email body, JSON payload, or OCR text).
        source_type: One of "email", "webhook_json", "ocr_text".
    """
    fallback = _regex_parse(raw_input)
    # Fast path: rules are confident -> no LLM call needed.
    if fallback["shipment_id"] and fallback["exception_type"] != "OTHER":
        fallback["ok"] = True
        return fallback

    # Ambiguous -> escalate to the LLM (with the regex result as the fallback).
    from llm import chat  # local import avoids any import-time coupling
    try:
        prompt = (
            "You are an extraction engine for a logistics operations system. "
            "From the raw exception artifact below, extract:\n"
            "  shipment_id     : the tracking/shipment reference (looks like SHP-1234)\n"
            "  exception_type  : ONE of WEATHER_HOLD, BAD_ADDRESS, CARRIER_OUTAGE, OTHER\n"
            "  extracted_fields: object with any useful extras you see "
            "(order_id, updated_address, carrier, attempts, etc.)\n"
            "  confidence      : 0.0-1.0\n"
            "Return ONLY a JSON object with those keys, nothing else.\n\n"
            f"SOURCE_TYPE: {source_type}\n---\n{raw_input}\n---"
        )
        out = chat([{"role": "user", "content": prompt}])
        parsed = _extract_json(out.get("text", ""))
        if parsed and parsed.get("shipment_id"):
            parsed.setdefault("extracted_fields", {})
            parsed.setdefault("confidence", 0.8)
            parsed["method"] = "llm"
            parsed["ok"] = True
            return parsed
    except Exception as e:
        fallback["llm_error"] = f"{type(e).__name__}: {e}"

    fallback["ok"] = bool(fallback["shipment_id"])
    if not fallback["ok"]:
        fallback["error"] = "could not extract a shipment id"
    return fallback


# ==========================================================================
# Reads (index-backed, non-state-changing)
# ==========================================================================
def get_shipment(shipment_id: str) -> dict:
    """Look up a shipment plus its linked order and customer, and the current
    exception. Always call this to diagnose before taking any action.

    Args:
        shipment_id: e.g. "SHP-1001".
    """
    sh = L.get_shipment_raw(shipment_id)
    if not sh:
        return {"ok": False, "error": f"unknown shipment {shipment_id}"}
    order = L.get_order_raw(sh["order_id"]) or {}
    cust = L.get_customer_for_order(sh["order_id"]) or {}
    return {
        "ok": True,
        "shipment": {k: v for k, v in sh.items() if not k.startswith("_fail")},
        "order": order,
        "customer": {k: cust.get(k) for k in ("id", "name", "preferred_channel")},
        "exception": {"code": sh.get("exception_code"), "note": sh.get("exception_note")},
        "escalate_threshold_usd": L.ESCALATE_THRESHOLD,
    }


def list_carriers(region: str) -> dict:
    """List carriers that serve a region, with capacity and API status. Use this
    to find an alternate carrier before rerouting/reassigning.

    Args:
        region: e.g. "US-MW", "US-NE", "US-SW".
    """
    carriers = L.carriers_in_region(region)
    return {
        "ok": True,
        "region": region,
        "carriers": [
            {"id": c["id"], "name": c["name"], "api_status": c["api_status"],
             "capacity_left": c["capacity_left"], "avg_delay_hrs": c["avg_delay_hrs"]}
            for c in carriers
        ],
    }


def check_inventory(sku: str, warehouse: str) -> dict:
    """Check on-hand quantity for a SKU at a warehouse (for reship decisions).

    Args:
        sku: e.g. "SKU-TV-3".
        warehouse: e.g. "HUB-LAX".
    """
    inv = L.inventory_for(sku, warehouse)
    if not inv:
        return {"ok": True, "sku": sku, "warehouse": warehouse, "qty": 0}
    return {"ok": True, **inv}


# ==========================================================================
# validate_address -- the ONE real external call (OpenStreetMap Nominatim)
# ==========================================================================
_MOCK_GEOCODE = {
    "20 w 34": {"normalized": "20 W 34th St, New York, NY 10001, USA",
                "lat": 40.7484, "lon": -73.9857},
    "michigan ave": {"normalized": "123 N Michigan Ave, Chicago, IL 60601, USA",
                     "lat": 41.8858, "lon": -87.6245},
}


def validate_address(address: str) -> dict:
    """Validate/normalize a street address against real OpenStreetMap geocoding
    (Nominatim, no key). Falls back to a local table if the network is down so
    the demo survives offline.

    Args:
        address: A raw/messy street address to validate.
    """
    try:
        import requests

        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1, "addressdetails": 0},
            headers={"User-Agent": "logistics-exception-agent/1.0 (hackathon demo)"},
            timeout=4,
        )
        resp.raise_for_status()
        hits = resp.json()
        if hits:
            top = hits[0]
            return {"ok": True, "valid": True, "source": "nominatim",
                    "normalized": top.get("display_name"),
                    "lat": float(top["lat"]), "lon": float(top["lon"]),
                    "input": address}
        return {"ok": True, "valid": False, "source": "nominatim",
                "normalized": None, "input": address,
                "note": "address not found by geocoder"}
    except Exception as e:
        low = (address or "").lower()
        for key, val in _MOCK_GEOCODE.items():
            if key in low:
                return {"ok": True, "valid": True, "source": "mock_fallback",
                        "input": address, **val,
                        "note": f"geocoder unreachable ({type(e).__name__}); used fallback"}
        return {"ok": True, "valid": False, "source": "mock_fallback",
                "normalized": address, "input": address,
                "note": f"geocoder unreachable ({type(e).__name__}); no fallback match"}


# ==========================================================================
# State-changing tools -- all go through L.execute_state_change
# (idempotency + exponential-backoff retry + transient/permanent failure class)
# ==========================================================================
def reroute_shipment(shipment_id: str, new_carrier: str, new_hub: str = "") -> dict:
    """Reroute a held shipment onto a different carrier (clears a weather hold).

    Args:
        shipment_id: Shipment to reroute.
        new_carrier: Carrier id to move it to (must serve the region).
        new_hub: Optional new hub id.
    """
    if new_carrier not in L.DB["carriers"]:
        return {"ok": False, "error": f"unknown carrier {new_carrier}"}

    def mutate():
        sh = L.get_shipment_raw(shipment_id)
        sh["carrier"] = new_carrier
        if new_hub:
            sh["current_hub"] = new_hub
        sh["status"] = "in_transit"
        sh["exception_code"] = None
        sh["exception_note"] = f"Rerouted to {new_carrier}"
        sh["eta"] = "2026-09-01"
        return {"carrier": new_carrier, "status": "in_transit",
                "exception_code": None, "eta": "2026-09-01"}

    return L.execute_state_change("reroute_shipment", shipment_id, mutate,
                                  f"reroute:{shipment_id}:{new_carrier}")


def reschedule_delivery(shipment_id: str, new_eta: str) -> dict:
    """Reschedule a failed delivery for a new date. (Carrier booking here can hit
    a transient error and is retried automatically.)

    Args:
        shipment_id: Shipment to reschedule.
        new_eta: New ETA date, e.g. "2026-09-02".
    """
    def mutate():
        sh = L.get_shipment_raw(shipment_id)
        sh["status"] = "out_for_delivery"
        sh["eta"] = new_eta
        sh["attempts"] = 0
        sh["exception_code"] = None
        sh["exception_note"] = f"Rescheduled for {new_eta}"
        return {"status": "out_for_delivery", "eta": new_eta, "exception_code": None}

    return L.execute_state_change("reschedule_delivery", shipment_id, mutate,
                                  f"reschedule:{shipment_id}:{new_eta}")


def update_address(shipment_id: str, address: str) -> dict:
    """Update a shipment's destination address (clears a bad-address exception).
    Validate the address first with validate_address.

    Args:
        shipment_id: Shipment to update.
        address: The corrected/normalized destination address.
    """
    def mutate():
        sh = L.get_shipment_raw(shipment_id)
        sh["destination"] = address
        # Correcting the address fixes the CAUSE, but a twice-failed delivery is
        # only truly resolved once it's rescheduled and moving again.
        sh["exception_code"] = "READY_TO_RESCHEDULE"
        sh["exception_note"] = "Address corrected; delivery must be rescheduled"
        return {"destination": address, "exception_code": "READY_TO_RESCHEDULE"}

    return L.execute_state_change("update_address", shipment_id, mutate,
                                  f"address:{shipment_id}:{address}")


def reassign_carrier(shipment_id: str, carrier: str) -> dict:
    """Reassign a shipment to a different carrier during an outage. NOTE: this can
    fail permanently if the target carrier's API is down -- if so, escalate.

    Args:
        shipment_id: Shipment to reassign.
        carrier: Carrier id to assign.
    """
    if carrier not in L.DB["carriers"]:
        return {"ok": False, "error": f"unknown carrier {carrier}"}

    def mutate():
        sh = L.get_shipment_raw(shipment_id)
        sh["carrier"] = carrier
        sh["status"] = "in_transit"
        sh["exception_code"] = None
        sh["exception_note"] = f"Reassigned to {carrier}"
        return {"carrier": carrier, "status": "in_transit", "exception_code": None}

    return L.execute_state_change("reassign_carrier", shipment_id, mutate,
                                  f"reassign:{shipment_id}:{carrier}")


def issue_credit(order_id: str, amount: float, reason: str) -> dict:
    """Issue a goodwill credit on an order.

    Args:
        order_id: Order to credit, e.g. "ORD-2002".
        amount: Credit amount in USD.
        reason: One of "delay", "failed_delivery", "goodwill", "damage".
    """
    order = L.get_order_raw(order_id)
    if not order:
        return {"ok": False, "error": f"unknown order {order_id}"}
    shipment_id = L.DB["_indexes"]["ship_by_order"].get(order_id)

    # Human-oversight gate for a financial action above the approval limit.
    high_value = amount >= APPROVAL_THRESHOLD_USD
    if high_value and REQUIRE_APPROVAL:
        # Block: surface for human sign-off and mutate nothing.
        return {"ok": True, "applied": False, "status": "awaiting_human_approval",
                "require_approval": True, "order_id": order_id, "amount": amount,
                "approval_reason": f"credit ${amount} >= ${APPROVAL_THRESHOLD_USD} approval limit "
                                   f"-- needs human sign-off"}

    def mutate():
        order["credit_usd"] = order.get("credit_usd", 0) + amount
        return {"order_id": order_id, "credit_usd": order["credit_usd"], "reason": reason}

    r = L.execute_state_change("issue_credit", shipment_id, mutate,
                               f"credit:{order_id}:{amount}:{reason}")
    if high_value and r.get("ok"):
        r["require_approval"] = True
        r["approval_reason"] = (f"credit ${amount} >= ${APPROVAL_THRESHOLD_USD} approval limit "
                                f"(auto-approved in demo; set REQUIRE_APPROVAL=1 to require sign-off)")
    return r


def escalate_to_human(shipment_id: str, reason: str, priority: str = "high") -> dict:
    """Escalate a shipment to a human operator when no safe automated fix exists
    (hard failure, high-value order, or no viable remedy). This is a valid,
    terminal outcome -- not a failure.

    Args:
        shipment_id: Shipment to escalate.
        reason: Why it needs a human.
        priority: One of "low", "medium", "high", "urgent".
    """
    def mutate():
        sh = L.get_shipment_raw(shipment_id)
        sh["status"] = "escalated"
        sh["exception_code"] = "ESCALATED"
        sh["exception_note"] = f"Escalated ({priority}): {reason}"
        return {"status": "escalated", "exception_code": "ESCALATED",
                "priority": priority, "reason": reason}

    return L.execute_state_change("escalate_to_human", shipment_id, mutate,
                                  f"escalate:{shipment_id}")


# ==========================================================================
# verify_shipment -- THE differentiator: re-read authoritative state + diff
# ==========================================================================
def verify_shipment(shipment_id: str) -> dict:
    """Re-read authoritative shipment state and return a field-level before/after
    diff versus the pre-agent snapshot, plus whether the exception is resolved.
    You MUST call this after any state change, before notifying or finishing.

    Args:
        shipment_id: Shipment to verify.
    """
    sh = L.get_shipment_raw(shipment_id)
    if not sh:
        return {"ok": False, "error": f"unknown shipment {shipment_id}"}
    return {
        "ok": True,
        "shipment_id": shipment_id,
        "resolved": L.is_resolved(shipment_id),
        "diff": L.diff(shipment_id),
        "current_state": {k: v for k, v in sh.items() if not k.startswith("_fail")},
    }


# ==========================================================================
# notify_customer -- real outbound side effect, gated by LIVE_ACTIONS
# ==========================================================================
def notify_customer(shipment_id: str, channel: str, message: str) -> dict:
    """Notify the customer about the resolution. Real WhatsApp/email is sent ONLY
    when LIVE ACTIONS is enabled; otherwise the send is simulated.

    Args:
        shipment_id: Shipment the customer is being notified about.
        channel: "whatsapp" or "email".
        message: The message body.
    """
    sh = L.get_shipment_raw(shipment_id)
    if not sh:
        return {"ok": False, "error": f"unknown shipment {shipment_id}"}
    cust = L.get_customer_for_order(sh["order_id"]) or {}
    to = cust.get("phone") if channel == "whatsapp" else cust.get("email")
    if not to:
        return {"ok": False, "error": f"no {channel} contact for customer"}

    if not LIVE_ACTIONS:
        return {"ok": True, "simulated": True, "channel": channel,
                "would_send": {"to": to, "message": message}}

    if channel == "whatsapp":
        res = send_whatsapp(to=to, body=message)
    else:
        res = send_email(to=to, subject=f"Update on your shipment {shipment_id}", body=message)
    return {"ok": res.get("ok", False), "simulated": False, "channel": channel,
            "to": to, "provider_result": res}


# ==========================================================================
# Registry -- schemas the LLM sees + name->fn map for the agent loop
# --------------------------------------------------------------------------
# ACTIVE list = the logistics toolset. web_search / rag_search / rag_add /
# send_whatsapp / send_email remain defined above (send_* are used by
# notify_customer) but are intentionally kept OUT of TOOLS so the agent isn't
# distracted by irrelevant tools -- this sharpens dynamic tool selection.
# ==========================================================================
_ENUM_REASON = ["delay", "failed_delivery", "goodwill", "damage"]
_ENUM_PRIORITY = ["low", "medium", "high", "urgent"]
_ENUM_CHANNEL = ["whatsapp", "email"]
_ENUM_SOURCE = ["email", "webhook_json", "ocr_text"]

TOOLS = [
    {
        "name": "parse_exception",
        "description": "Extract the shipment id + exception type from a raw exception artifact (messy email, JSON webhook, or OCR text). ALWAYS do this first.",
        "parameters": {
            "type": "object",
            "properties": {
                "raw_input": {"type": "string", "description": "The raw artifact text."},
                "source_type": {"type": "string", "enum": _ENUM_SOURCE,
                                "description": "Kind of artifact."},
            },
            "required": ["raw_input"],
        },
    },
    {
        "name": "extract_from_document",
        "description": "OCR/vision: extract text from a local image or PDF (e.g. a photo of a damaged shipping label) using Gemini. Feed the text to parse_exception.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to a local image/PDF."},
                "prompt": {"type": "string", "description": "Optional extraction instruction."},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "get_shipment",
        "description": "Look up a shipment + its order, customer, and current exception. Diagnose with this before acting.",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "string", "description": "e.g. SHP-1001."},
            },
            "required": ["shipment_id"],
        },
    },
    {
        "name": "list_carriers",
        "description": "List carriers serving a region with capacity + API status. Use to find an alternate carrier before rerouting/reassigning.",
        "parameters": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "e.g. US-MW, US-NE, US-SW."},
            },
            "required": ["region"],
        },
    },
    {
        "name": "check_inventory",
        "description": "Check on-hand quantity for a SKU at a warehouse.",
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "e.g. SKU-TV-3."},
                "warehouse": {"type": "string", "description": "e.g. HUB-LAX."},
            },
            "required": ["sku", "warehouse"],
        },
    },
    {
        "name": "validate_address",
        "description": "Validate/normalize a street address against real OpenStreetMap geocoding. Use before update_address on a bad-address exception.",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Raw/messy address to validate."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "reroute_shipment",
        "description": "Reroute a held shipment to a different carrier (clears a weather hold). State-changing.",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "string"},
                "new_carrier": {"type": "string", "description": "Carrier id serving the region."},
                "new_hub": {"type": "string", "description": "Optional new hub id."},
            },
            "required": ["shipment_id", "new_carrier"],
        },
    },
    {
        "name": "reschedule_delivery",
        "description": "Reschedule a failed delivery for a new ETA. State-changing (auto-retries transient booking errors).",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "string"},
                "new_eta": {"type": "string", "description": "New ETA date, e.g. 2026-09-02."},
            },
            "required": ["shipment_id", "new_eta"],
        },
    },
    {
        "name": "update_address",
        "description": "Update a shipment's destination address (clears a bad-address exception). Validate first. State-changing.",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "string"},
                "address": {"type": "string", "description": "Corrected/normalized address."},
            },
            "required": ["shipment_id", "address"],
        },
    },
    {
        "name": "reassign_carrier",
        "description": "Reassign a shipment to another carrier during an outage. May FAIL PERMANENTLY if the target carrier is down -- then escalate. State-changing.",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "string"},
                "carrier": {"type": "string", "description": "Carrier id to assign."},
            },
            "required": ["shipment_id", "carrier"],
        },
    },
    {
        "name": "issue_credit",
        "description": "Issue a goodwill credit on an order. State-changing.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "e.g. ORD-2002."},
                "amount": {"type": "number", "description": "Credit amount in USD."},
                "reason": {"type": "string", "enum": _ENUM_REASON},
            },
            "required": ["order_id", "amount", "reason"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "Escalate to a human when no safe automated fix exists (hard failure, high-value order, or no remedy). Valid terminal outcome. State-changing.",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "string"},
                "reason": {"type": "string", "description": "Why a human is needed."},
                "priority": {"type": "string", "enum": _ENUM_PRIORITY},
            },
            "required": ["shipment_id", "reason"],
        },
    },
    {
        "name": "verify_shipment",
        "description": "Re-read authoritative state and return a before/after diff + resolved flag. MUST be called after any state change, before notifying or finishing.",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "string"},
            },
            "required": ["shipment_id"],
        },
    },
    {
        "name": "notify_customer",
        "description": "Notify the customer of the resolution. Real send only when LIVE ACTIONS is on; otherwise simulated.",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "string"},
                "channel": {"type": "string", "enum": _ENUM_CHANNEL},
                "message": {"type": "string", "description": "Message body."},
            },
            "required": ["shipment_id", "channel", "message"],
        },
    },
]

# name -> callable (used by agent.py). Includes tools NOT in the active TOOLS
# list (send_*, web_search, rag_*) so they can still be dispatched if referenced.
DISPATCH = {
    "parse_exception": parse_exception,
    "extract_from_document": extract_from_document,
    "get_shipment": get_shipment,
    "list_carriers": list_carriers,
    "check_inventory": check_inventory,
    "validate_address": validate_address,
    "reroute_shipment": reroute_shipment,
    "reschedule_delivery": reschedule_delivery,
    "update_address": update_address,
    "reassign_carrier": reassign_carrier,
    "issue_credit": issue_credit,
    "escalate_to_human": escalate_to_human,
    "verify_shipment": verify_shipment,
    "notify_customer": notify_customer,
    # still available, just not advertised to the model:
    "web_search": web_search,
    "send_whatsapp": send_whatsapp,
    "send_email": send_email,
    "rag_search": rag_search,
}

# State-changing tool names -- agent.py uses this to enforce verify-before-finish.
STATE_CHANGING = {
    "reroute_shipment", "reschedule_delivery", "update_address",
    "reassign_carrier", "issue_credit", "escalate_to_human",
}


def dispatch(name: str, args: dict) -> dict:
    """Run a tool by name with a dict of args. Never raises."""
    fn = DISPATCH.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return {"ok": False, "error": f"bad args for {name}: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
