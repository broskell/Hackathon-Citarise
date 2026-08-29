"""Streamlit demo UI for the Logistics Exception Agent — Resend-inspired dark UI.

    streamlit run app.py

Left  = final answer + the persistent STATE DIFF panel (before/after, RESOLVED
        badge, resilient-execution audit timeline).
Right = live Agent Trace, provider-tagged, with the verify step rendered as a
        before/after table and retries shown as chips.

The verify/diff panel is the core differentiator: we don't just claim the fix,
we re-read authoritative state and show exactly which fields changed.
"""

import json

import streamlit as st

import logistics
import tools
from agent import MAX_ITERS, run_agent

st.set_page_config(page_title="Logistics Exception Agent", page_icon="📦",
                   layout="wide", initial_sidebar_state="expanded")

# ==========================================================================
# Design system — Resend/Claude-inspired. Aggressively overrides Streamlit
# chrome so it reads as a product, not a Streamlit app.
# ==========================================================================
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,0,0&display=swap');

:root {
  --bg:#0A0A0B; --panel:#0F0F11; --panel2:#161619; --panel3:#1C1C21;
  --line:rgba(255,255,255,.08); --line2:rgba(255,255,255,.13);
  --text:#F2F2F3; --muted:#8A8A93; --muted2:#6A6A72;
  --white:#FAFAFA; --accent:#7C6BF0; --accent2:#9B8CF7;
  --green:#3DD68C; --red:#F26D78; --amber:#E7A94B; --blue:#5B8DEF;
}
html, body, [class*="css"], .stApp { font-family:'Inter',system-ui,sans-serif; }
.stApp, [data-testid="stAppViewContainer"] { background:var(--bg); }

/* --- kill Streamlit chrome --- */
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
#MainMenu, footer, [data-testid="stStatusWidget"] { display:none !important; }
.block-container { padding:2rem 2.6rem 4rem !important; max-width:1240px; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap:.55rem; }

.material-symbols-rounded { font-family:'Material Symbols Rounded'; font-weight:normal;
  font-style:normal; line-height:1; vertical-align:-4px; font-size:20px;
  -webkit-font-feature-settings:'liga'; }

/* --- sidebar as a nav rail --- */
[data-testid="stSidebar"] { background:#0C0C0E; border-right:1px solid var(--line); }
[data-testid="stSidebar"] .block-container,
[data-testid="stSidebar"] > div:first-child { padding-top:1.1rem !important; }
[data-testid="stSidebar"] hr { margin:.7rem 0; border-color:var(--line); }
.lx-brand { display:flex; align-items:center; gap:10px; padding:2px 4px 10px; }
.lx-brand .mark { width:34px; height:34px; border-radius:9px; flex:none; display:flex;
  align-items:center; justify-content:center; background:linear-gradient(150deg,#2A2440,#14131A);
  border:1px solid var(--line2); box-shadow:0 2px 10px rgba(124,107,240,.25) inset; }
.lx-brand .mark .material-symbols-rounded { font-size:20px;
  background:linear-gradient(150deg,var(--accent2),#C9C1FF); -webkit-background-clip:text;
  background-clip:text; color:transparent; }
.lx-brand .t { font-weight:700; font-size:14.5px; letter-spacing:-.01em; }
.lx-brand .s { color:var(--muted2); font-size:11px; margin-top:1px; }
.lx-navlabel { color:var(--muted2); font-size:10.5px; font-weight:700; letter-spacing:.09em;
  text-transform:uppercase; padding:6px 4px 3px; }

/* radio -> nav items */
[data-testid="stSidebar"] [role="radiogroup"] { gap:3px; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] { display:flex; align-items:center;
  width:100%; padding:8px 11px; border-radius:9px; border:1px solid transparent; margin:0;
  cursor:pointer; transition:.12s; }
[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover { background:var(--panel2); }
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] {
  background:var(--panel2); border-color:var(--line2); }
/* hide only the BaseWeb radio circle (label > div > div > div:first-child),
   keeping the text sibling (stMarkdownContainer) visible */
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div > div:first-child { display:none !important; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] p { font-size:12.5px; color:var(--muted);
  font-weight:500; }
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] p { color:var(--text);
  font-weight:600; }

/* toggle + captions */
[data-testid="stSidebar"] .stCaption, [data-testid="stCaptionContainer"] p { color:var(--muted2);
  font-size:11px; }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { font-size:12.5px; color:var(--text);
  font-weight:600; }

/* --- primary button: white, Resend-style --- */
.stButton > button { border-radius:10px; font-weight:600; font-size:13.5px; transition:.12s;
  border:1px solid var(--line2); background:var(--panel2); color:var(--text); }
.stButton > button:hover { background:var(--panel3); border-color:var(--line2); color:var(--text); }
.stButton > button[kind="primary"] { background:var(--white); color:#0A0A0A;
  border:1px solid var(--white); padding:.55rem 1.2rem; }
.stButton > button[kind="primary"]:hover { background:#fff; color:#000;
  box-shadow:0 8px 26px rgba(255,255,255,.14); transform:translateY(-1px); }
.stButton > button:focus { box-shadow:none !important; }

/* expanders */
[data-testid="stExpander"] { border:1px solid var(--line); border-radius:10px; background:var(--panel); }
[data-testid="stExpander"] summary { font-size:12px; color:var(--muted); }
.stCode, pre { border-radius:9px !important; }

/* --- headline + subtitle --- */
.lx-title { font-size:23px; font-weight:800; letter-spacing:-.025em; margin:0; }
.lx-sub { color:var(--muted); font-size:13px; margin:3px 0 0; }
.lx-eyebrow { display:flex; align-items:center; gap:7px; color:var(--muted); font-size:12.5px;
  font-weight:600; margin:2px 0 8px; }
.lx-eyebrow .material-symbols-rounded { font-size:17px; color:var(--muted); }

/* --- chips / badges --- */
.lx-chip { display:inline-flex; align-items:center; gap:5px; padding:2px 9px; border-radius:999px;
  font-size:11px; font-weight:600; border:1px solid var(--line2); background:var(--panel2); color:var(--muted); }
.lx-chip .material-symbols-rounded { font-size:14px; }
.lx-chip.prov-gemini { color:var(--blue); border-color:rgba(91,141,239,.35); background:rgba(91,141,239,.08); }
.lx-chip.prov-groq { color:var(--amber); border-color:rgba(231,169,75,.32); background:rgba(231,169,75,.08); }
.lx-chip.ok { color:var(--green); border-color:rgba(61,214,140,.32); background:rgba(61,214,140,.08); }
.lx-chip.fail { color:var(--red); border-color:rgba(242,109,120,.34); background:rgba(242,109,120,.09); }
.lx-chip.retry { color:var(--amber); border-color:rgba(231,169,75,.32); background:rgba(231,169,75,.08); }

.lx-badge-lg { display:inline-flex; align-items:center; gap:8px; padding:8px 15px; border-radius:11px;
  font-weight:700; font-size:14px; letter-spacing:.01em; }
.lx-badge-lg .material-symbols-rounded { font-size:19px; }
.lx-badge-lg.resolved { background:rgba(61,214,140,.1); color:var(--green); border:1px solid rgba(61,214,140,.3); }
.lx-badge-lg.escal { background:rgba(231,169,75,.1); color:var(--amber); border:1px solid rgba(231,169,75,.3); }
.lx-badge-lg.open { background:rgba(242,109,120,.1); color:var(--red); border:1px solid rgba(242,109,120,.3); }

/* --- cards --- */
.lx-card { background:var(--panel); border:1px solid var(--line); border-radius:14px;
  padding:15px 17px; margin:11px 0; }
.lx-card .hdr { display:flex; align-items:center; flex-wrap:wrap; gap:9px; font-weight:600; font-size:13.5px; }
.lx-card .hdr .material-symbols-rounded { color:var(--accent2); font-size:19px; }
.lx-tool { font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--accent2);
  background:rgba(124,107,240,.12); padding:1px 7px; border-radius:6px; }

/* diff table */
table.lx-diff { width:100%; border-collapse:collapse; margin-top:11px; }
table.lx-diff th { text-align:left; color:var(--muted2); font-weight:700; font-size:10px;
  text-transform:uppercase; letter-spacing:.06em; padding:0 10px 7px; }
table.lx-diff td { padding:8px 10px; border-top:1px solid var(--line); vertical-align:top;
  font-family:'JetBrains Mono',monospace; font-size:12px; }
table.lx-diff td.field { color:var(--text); font-family:'Inter',sans-serif; font-weight:600;
  white-space:nowrap; }
table.lx-diff td.before { color:var(--red); text-decoration:line-through; opacity:.72; }
table.lx-diff td.after { color:var(--green); }

/* audit timeline */
.lx-tl-row { display:flex; align-items:center; gap:9px; padding:9px 0; border-top:1px solid var(--line); font-size:13px; }
.lx-tl-row:first-child { border-top:none; }
.lx-tl-row .seq { width:22px; height:22px; border-radius:7px; background:var(--panel2); color:var(--muted);
  display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; flex:none; }
.lx-tl-row .nm { font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--text); flex:1; }

/* raw artifact */
.lx-art { background:var(--panel); border:1px solid var(--line); border-radius:12px;
  padding:14px 16px; font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--muted);
  white-space:pre-wrap; max-height:250px; overflow:auto; line-height:1.55; }

/* empty state (Resend-like) */
.lx-empty { position:relative; border:1px solid var(--line); border-radius:16px; padding:46px 24px;
  text-align:center; overflow:hidden; background:
    radial-gradient(120% 90% at 50% -10%, rgba(124,107,240,.14), transparent 60%), var(--panel); }
.lx-empty .mk { width:60px; height:60px; margin:0 auto 16px; border-radius:15px; display:flex;
  align-items:center; justify-content:center; background:linear-gradient(150deg,#231F35,#121118);
  border:1px solid var(--line2); box-shadow:0 10px 40px rgba(124,107,240,.22); }
.lx-empty .mk .material-symbols-rounded { font-size:30px;
  background:linear-gradient(150deg,var(--accent2),#CDC6FF); -webkit-background-clip:text;
  background-clip:text; color:transparent; }
.lx-empty h3 { margin:0 0 5px; font-size:16px; font-weight:700; }
.lx-empty p { margin:0 auto; max-width:320px; color:var(--muted); font-size:12.5px; line-height:1.55; }

/* shimmer loader */
.lx-loader { display:inline-flex; align-items:center; gap:9px; color:var(--muted); font-size:13.5px;
  font-weight:600; }
.lx-loader .dot { width:7px; height:7px; border-radius:50%;
  background:linear-gradient(90deg,var(--accent),var(--accent2)); animation:lx-pulse 1s ease-in-out infinite; }
@keyframes lx-pulse { 0%,100%{opacity:.25; transform:scale(.8);} 50%{opacity:1; transform:scale(1);} }
.lx-foot { color:var(--muted2); font-size:11.5px; margin-top:12px; font-family:'JetBrains Mono',monospace; }
hr { border-color:var(--line); }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def icon(name):
    return f'<span class="material-symbols-rounded">{name}</span>'


TOOL_ICON = {
    "parse_exception": "manage_search", "extract_from_document": "document_scanner",
    "get_shipment": "inventory_2", "list_carriers": "local_shipping",
    "check_inventory": "warehouse", "validate_address": "location_on",
    "reroute_shipment": "alt_route", "reschedule_delivery": "event_repeat",
    "update_address": "edit_location", "reassign_carrier": "swap_horiz",
    "issue_credit": "paid", "escalate_to_human": "support_agent",
    "verify_shipment": "fact_check", "notify_customer": "send",
}


# ==========================================================================
# Sidebar — nav rail
# ==========================================================================
with st.sidebar:
    st.markdown(
        f"""<div class="lx-brand"><div class="mark">{icon('conveyor_belt')}</div>
        <div><div class="t">Exception Agent</div><div class="s">Logistics ops · autonomous</div></div></div>""",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="lx-navlabel">Scenarios</div>', unsafe_allow_html=True)
    scen_key = st.radio(
        "scenario", list(logistics.SCENARIOS.keys()),
        format_func=lambda k: f"{k} · {logistics.SCENARIOS[k]['label']}",
        label_visibility="collapsed",
    )
    st.divider()
    live = st.toggle("Live actions", value=False,
                     help="OFF = notifications simulated. ON = real Twilio/Resend sends.")
    st.caption("Real WhatsApp/email will be sent" if live
               else "Notifications simulated · DB changes local & safe")
    st.divider()
    st.markdown('<div class="lx-navlabel">Tools</div>', unsafe_allow_html=True)
    for t in tools.TOOLS:
        st.markdown(
            f'<div style="font-size:12px;color:var(--muted);padding:2px 4px;">'
            f'{icon(TOOL_ICON.get(t["name"],"chevron_right"))} '
            f'<span style="font-family:JetBrains Mono,monospace;font-size:11.5px;">{t["name"]}</span></div>',
            unsafe_allow_html=True,
        )
    st.markdown('<div class="lx-foot">Gemini → Groq failover · '
                f'max {MAX_ITERS} steps</div>', unsafe_allow_html=True)


# ==========================================================================
# Top bar — title + primary action (Resend-style white button, top right)
# ==========================================================================
scen = logistics.SCENARIOS[scen_key]
head_l, head_r = st.columns([3, 1])
with head_l:
    st.markdown(
        '<div><p class="lx-title">Logistics Exception Agent</p>'
        '<p class="lx-sub">Extract from raw input · resolve dynamically · retry &amp; escalate · '
        'verify with a before/after diff</p></div>',
        unsafe_allow_html=True,
    )
with head_r:
    st.write("")
    run = st.button(f"Run agent  ›  {scen_key}", type="primary", use_container_width=True)


# ==========================================================================
# Raw artifact
# ==========================================================================
st.markdown(f'<div class="lx-eyebrow">{icon("description")} Raw exception artifact · '
            f'{scen["source_type"]}</div>', unsafe_allow_html=True)
if scen["source_type"] == "ocr_text":
    a_col, b_col = st.columns([1, 1])
    with a_col:
        st.image(scen["artifact"], caption="Photo of a damaged shipping label — the agent OCRs this",
                 use_container_width=True)
    with b_col:
        st.markdown('<div class="lx-art">A PHOTO — no machine-readable fields.\n\n'
                    'The agent must use vision (extract_from_document) to recover\n'
                    'the tracking id and exception before it can act.</div>', unsafe_allow_html=True)
else:
    st.markdown(f'<div class="lx-art">{scen["artifact"]}</div>', unsafe_allow_html=True)

st.write("")
left, right = st.columns([1, 1], gap="large")
with right:
    st.markdown(f'<div class="lx-eyebrow">{icon("bolt")} Agent trace</div>', unsafe_allow_html=True)
    trace_box = st.container()
with left:
    st.markdown(f'<div class="lx-eyebrow">{icon("check_circle")} Result</div>', unsafe_allow_html=True)
    answer_box = st.empty()
    diff_box = st.container()


# ==========================================================================
# Renderers
# ==========================================================================
def _diff_table(diff):
    if not diff:
        return '<p style="color:var(--muted);font-size:12.5px;margin-top:8px;">No fields changed.</p>'
    rows = "".join(
        f'<tr><td class="field">{d["field"]}</td>'
        f'<td class="before">{d.get("before")}</td>'
        f'<td class="after">{d.get("after")}</td></tr>'
        for d in diff
    )
    return (f'<table class="lx-diff"><tr><th>Field</th><th>Before</th><th>After</th></tr>{rows}</table>')


def _prov_chip(prov):
    cls = f"prov-{prov}" if prov in ("gemini", "groq") else ""
    return f'<span class="lx-chip {cls}">{icon("smart_toy")} {prov}</span>'


def _status_badge(resolved, code):
    if code == "ESCALATED":
        return f'<span class="lx-badge-lg escal">{icon("support_agent")} ESCALATED TO HUMAN</span>'
    if resolved:
        return f'<span class="lx-badge-lg resolved">{icon("verified")} RESOLVED &amp; VERIFIED</span>'
    return f'<span class="lx-badge-lg open">{icon("pending")} UNRESOLVED</span>'


def render_step(step):
    prov = step.get("provider", "?")
    tool = step.get("tool")
    obs = step.get("observation")
    with trace_box.container():
        if not tool:
            st.markdown(
                f'<div class="lx-card"><div class="hdr">{icon("flag")} '
                f'Step {step["step"]} · Final answer {_prov_chip(prov)}</div></div>',
                unsafe_allow_html=True,
            )
            return

        chips = _prov_chip(prov)
        if isinstance(obs, dict):
            if obs.get("ok") is True:
                chips += f'<span class="lx-chip ok">{icon("check")} ok</span>'
            elif obs.get("ok") is False:
                chips += f'<span class="lx-chip fail">{icon("error")} {obs.get("failure_class") or "fail"}</span>'
            n = len(obs.get("retry_log") or [])
            if n:
                chips += f'<span class="lx-chip retry">{icon("replay")} retried ×{n}</span>'
            if obs.get("source") in ("nominatim", "mock_fallback", "cache_fallback", "gemini_vision"):
                chips += f'<span class="lx-chip">{icon("cloud")} {obs["source"]}</span>'
            if obs.get("idempotent_replay"):
                chips += f'<span class="lx-chip">{icon("history")} idempotent replay</span>'

        st.markdown(
            f'<div class="lx-card"><div class="hdr">{icon(TOOL_ICON.get(tool,"chevron_right"))} '
            f'Step {step["step"]} · <span class="lx-tool">{tool}</span> {chips}</div>',
            unsafe_allow_html=True,
        )
        if tool == "verify_shipment" and isinstance(obs, dict):
            code = (obs.get("current_state") or {}).get("exception_code")
            st.markdown(_status_badge(obs.get("resolved"), code) + _diff_table(obs.get("diff")),
                        unsafe_allow_html=True)
        else:
            with st.expander("input / observation"):
                st.code(json.dumps(step.get("tool_input", {}), indent=2, default=str), language="json")
                st.code(obs if isinstance(obs, str) else json.dumps(obs, indent=2, default=str), language="json")
        st.markdown("</div>", unsafe_allow_html=True)


def render_state_diff(sd):
    if not sd:
        return
    code = (sd.get("current_state") or {}).get("exception_code")
    st.markdown(
        f'<div class="lx-card"><div class="hdr">{icon("difference")} '
        f'Verified state diff · <span class="lx-tool">{sd["shipment_id"]}</span></div>'
        f'{_status_badge(sd.get("resolved"), code)}{_diff_table(sd.get("diff"))}</div>',
        unsafe_allow_html=True,
    )
    rows = ""
    for a in sd.get("audit_log", []):
        state = (f'<span class="lx-chip ok">{icon("check")} ok</span>' if a.get("ok")
                 else f'<span class="lx-chip fail">{icon("error")} {a.get("failure_class") or "fail"}</span>')
        rl = a.get("retry_log") or []
        retry = (f'<span class="lx-chip retry">{icon("replay")} {len(rl)} retr{"y" if len(rl)==1 else "ies"}</span>'
                 if rl else "")
        att = f'<span class="lx-chip">{icon("repeat")} {a.get("attempts")}×</span>'
        rows += (f'<div class="lx-tl-row"><div class="seq">{a["seq"]}</div>'
                 f'<span class="nm">{a["tool"]}</span>{state}{att}{retry}</div>')
    if rows:
        st.markdown(
            f'<div class="lx-card"><div class="hdr">{icon("account_tree")} '
            f'Resilient-execution audit</div><div style="margin-top:6px;">{rows}</div></div>',
            unsafe_allow_html=True,
        )


EMPTY = (f'<div class="lx-empty"><div class="mk">{icon("conveyor_belt")}</div>'
         '<h3>No run yet</h3><p>Pick a scenario and run the agent. The verified '
         'before/after diff and resolution appear here.</p></div>')


# ==========================================================================
# Run
# ==========================================================================
if run:
    tools.LIVE_ACTIONS = bool(live)
    goal = logistics.scenario_goal(scen_key)
    answer_box.markdown(
        '<div class="lx-loader"><span class="dot"></span>Agent is working…</div>',
        unsafe_allow_html=True,
    )
    result = run_agent(goal, on_step=render_step)
    answer_box.markdown(result.get("answer") or "_(no answer)_")
    with diff_box:
        render_state_diff(result.get("state_diff"))
        prov = result["trace"][-1]["provider"] if result["trace"] else "none"
        st.markdown(f'<div class="lx-foot">final provider: {prov} · {result["iterations"]} iterations · '
                    f'live actions {"on" if live else "off"}</div>', unsafe_allow_html=True)
else:
    answer_box.markdown(EMPTY, unsafe_allow_html=True)
