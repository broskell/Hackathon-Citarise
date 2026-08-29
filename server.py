"""FastAPI backend for the Logistics Exception Agent.

Serves the editorial landing page + live console from web/, and streams the
agent's trace to the browser over Server-Sent Events. The Python agent core
(agent.py / tools.py / logistics.py) is untouched — this is only transport.

Run:  python -m uvicorn server:app --port 8000 --reload
"""

import json
import queue
import threading

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import llm
import logistics
import tools
from agent import MAX_ITERS, run_agent
from demo_runner import run_replay

app = FastAPI(title="Logistics Exception Agent")


@app.middleware("http")
async def no_cache(request, call_next):
    """Never cache the app shell/assets — a demo must always reflect the latest build."""
    resp = await call_next(request)
    ct = resp.headers.get("content-type", "")
    if any(t in ct for t in ("text/html", "text/css", "javascript")):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@app.get("/api/meta")
def meta():
    """Scenario metadata + raw artifacts the landing/console renders."""
    out = {}
    for key, sc in logistics.SCENARIOS.items():
        item = {"key": key, "label": sc["label"], "source_type": sc["source_type"],
                "shipment": sc["shipment"]}
        if sc["source_type"] == "ocr_text":
            item["artifact_kind"] = "image"
            item["artifact"] = "/assets/label_SHP-3003.png"
            item["ocr_text"] = logistics.RAW_PHOTO_TEXT_C  # editable OCR override
        else:
            item["artifact_kind"] = "text"
            item["artifact"] = sc["artifact"]
        out[key] = item
    return {"scenarios": out, "max_iters": MAX_ITERS,
            "tools": [t["name"] for t in tools.TOOLS]}


@app.get("/api/shipments")
def shipments():
    """The full shipment directory (fresh seed) for the Shipments explorer."""
    logistics.reset_db()
    out = []
    for sid, sh in logistics.DB["shipments"].items():
        order = logistics.get_order_raw(sh["order_id"]) or {}
        cust = logistics.get_customer_for_order(sh["order_id"]) or {}
        out.append({
            "id": sid, "status": sh["status"], "exception_code": sh.get("exception_code"),
            "exception_note": sh.get("exception_note"), "carrier": sh["carrier"],
            "current_hub": sh["current_hub"], "destination": sh["destination"],
            "region": sh["region"], "eta": sh["eta"], "attempts": sh["attempts"],
            "order_id": sh["order_id"], "order_value_usd": order.get("value_usd"),
            "priority": order.get("priority"),
            "customer": cust.get("name"), "channel": cust.get("preferred_channel"),
        })
    # A demo scenario key per shipment (so "Run agent" knows which artifact to use).
    ship_to_scenario = {sc["shipment"]: k for k, sc in logistics.SCENARIOS.items()}
    for s in out:
        s["scenario"] = ship_to_scenario.get(s["id"])
    return {"shipments": out}


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.get("/api/run")
def run(scenario: str = "A", live: int = 0, raw_input: str = "", source_type: str = ""):
    """Stream the agent run as SSE: one `step` event per trace step (live, as they
    happen), then a final `result` event, then `done`.

    If `raw_input` is provided, the agent runs on that CUSTOM artifact instead of
    the built-in scenario. Custom runs need the live LLM planner (the deterministic
    replay is scripted only for the built-in scenarios)."""
    if scenario not in logistics.SCENARIOS:
        return JSONResponse({"error": f"unknown scenario {scenario}"}, status_code=400)
    custom = bool(raw_input.strip())

    def gen():
        q: "queue.Queue" = queue.Queue()

        def on_step(step):
            q.put(("step", step))

        def worker():
            try:
                tools.LIVE_ACTIONS = bool(int(live))
                if custom:
                    goal = logistics.custom_goal(raw_input, source_type or "email")
                else:
                    goal = logistics.scenario_goal(scenario)
                # Try the real LLM-planned agent, streaming live.
                result = run_agent(goal, on_step=on_step)
                stopped = (result.get("state_diff") is None
                           and str(result.get("answer", "")).startswith("Agent stopped"))
                if stopped and custom:
                    # Can't replay arbitrary input -> surface a clean message.
                    q.put(("agent_error", {"error": "The LLM planner is rate-limited right now, and "
                                           "custom input can't use the deterministic replay (that's "
                                           "scripted for the built-in scenarios). Retry shortly, or "
                                           "run a built-in scenario."}))
                    return
                if stopped:
                    # Built-in scenario + both providers down -> deterministic replay.
                    q.put(("reset", {}))
                    q.put(("notice", {"mode": "replay",
                                      "text": "Both LLM free tiers are rate-limited right now — "
                                              "streaming a deterministic replay through the REAL "
                                              "tools (real state diffs, retries, geocoding, vision "
                                              "fallback). Live LLM planning resumes automatically "
                                              "when quota returns."}))
                    result = run_replay(scenario, on_step=on_step)
                q.put(("result", result))
            except Exception as e:  # never leave the stream hanging
                q.put(("agent_error", {"error": f"{type(e).__name__}: {e}"}))
            finally:
                q.put(None)

        threading.Thread(target=worker, daemon=True).start()
        yield _sse("open", {"scenario": scenario})
        while True:
            item = q.get()
            if item is None:
                break
            yield _sse(item[0], item[1])
        yield _sse("done", {})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Static: assets (the label photo) + the built site. Mount LAST so /api wins.
app.mount("/assets", StaticFiles(directory="assets"), name="assets")
app.mount("/", StaticFiles(directory="web", html=True), name="web")
