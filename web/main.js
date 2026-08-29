/* LODESTAR — front-end: GSAP scroll animation + live agent console (SSE). */

const TOOL_ICON = {
  parse_exception: "manage_search", extract_from_document: "document_scanner",
  get_shipment: "inventory_2", list_carriers: "local_shipping",
  check_inventory: "warehouse", validate_address: "location_on",
  reroute_shipment: "alt_route", reschedule_delivery: "event_repeat",
  update_address: "edit_location", reassign_carrier: "swap_horiz",
  issue_credit: "paid", escalate_to_human: "support_agent",
  verify_shipment: "fact_check", notify_customer: "send",
};
const SRC_LABEL = { email: "carrier email", webhook_json: "json webhook", ocr_text: "photo · vision" };

const $ = (s, r = document) => r.querySelector(s);
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };
const esc = (s) => String(s ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const icon = (n) => `<span class="material-symbols-rounded">${n}</span>`;

let META = null, ACTIVE = "A", running = false, RUN_START = 0;

/* ---------------- GSAP scroll animation ---------------- */
function initAnim() {
  if (!window.gsap) return;
  gsap.registerPlugin(ScrollTrigger);

  // nav background on scroll
  ScrollTrigger.create({ start: "top -60", onUpdate: (s) =>
    $("#nav").classList.toggle("scrolled", s.scroll() > 60) });
  $("#nav").classList.toggle("scrolled", window.scrollY > 60);

  // hero: stagger words + elements on load
  const tl = gsap.timeline({ defaults: { ease: "power3.out" } });
  tl.from(".hero-title .w", { yPercent: 115, opacity: 0, duration: 0.9, stagger: 0.055 }, 0.1)
    .from("[data-hero]", { y: 22, opacity: 0, duration: 0.7, stagger: 0.09 }, 0.5)
    .from(".hero-wordmark", { opacity: 0, scale: 1.06, duration: 1.6, ease: "power2.out" }, 0);

  // parallax on the giant hero wordmark
  gsap.to(".hero-wordmark", { yPercent: -18, ease: "none",
    scrollTrigger: { trigger: ".hero", start: "top top", end: "bottom top", scrub: true } });

  // footer wordmark: horizontal drift on scroll (Bugatti-style bottom bar)
  gsap.fromTo(".footer-wordmark", { xPercent: 12 }, { xPercent: -12, ease: "none",
    scrollTrigger: { trigger: ".footer", start: "top bottom", end: "bottom bottom", scrub: 0.6 } });
}

/* Robust scroll reveals via IntersectionObserver — never leaves content stuck
   invisible (the failure mode of GSAP `from` + ScrollTrigger). Runs even if the
   GSAP CDN is blocked. */
function initReveals() {
  const targets = [...document.querySelectorAll("[data-reveal]"), ...document.querySelectorAll(".pipe-step")];
  document.querySelectorAll(".pipe-step").forEach((n, i) => { n.style.transitionDelay = (i * 0.07) + "s"; });
  if (!("IntersectionObserver" in window)) { targets.forEach(n => n.classList.add("in")); return; }
  const io = new IntersectionObserver((entries) => {
    entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
  }, { threshold: 0.12, rootMargin: "0px 0px -8% 0px" });
  targets.forEach(n => io.observe(n));
  // failsafe: if anything is still hidden after 3s, reveal it.
  setTimeout(() => targets.forEach(n => n.classList.add("in")), 3000);
}

/* ---------------- console ---------------- */
async function loadMeta() {
  META = await (await fetch("/api/meta")).json();
  const tabs = $("#scenTabs");
  tabs.innerHTML = "";
  Object.values(META.scenarios).forEach((s) => {
    const t = el(`<button class="scen-tab" data-key="${s.key}">
      <div class="st-top"><span class="st-key">${s.key}</span>
        <span class="st-mode">${SRC_LABEL[s.source_type] || s.source_type}</span></div>
      <div class="st-label">${esc(s.label)}</div></button>`);
    t.addEventListener("click", () => setScenario(s.key));
    tabs.appendChild(t);
  });
  setScenario("A");
}

const EDITS = {};   // per-scenario edited artifact text (survives tab switches)

function setScenario(key) {
  if (running) return;
  ACTIVE = key;
  document.querySelectorAll(".scen-tab").forEach(t =>
    t.classList.toggle("active", t.dataset.key === key));
  const s = META.scenarios[key];
  $("#srcPill").textContent = SRC_LABEL[s.source_type] || s.source_type;
  const art = $("#artifact");

  // The editable text the agent will run on. For text scenarios it's the
  // artifact itself; for the photo scenario it's an OCR override (leave it as-is
  // to OCR the live photo, or edit it to feed custom "extracted" text).
  const original = (s.artifact_kind === "image") ? (s.ocr_text || "") : s.artifact;
  const isImage = s.artifact_kind === "image";
  const label = isImage
    ? `${icon("edit_note")} Editable OCR — leave as-is to run vision on the photo, or edit to feed custom text.`
    : `${icon("edit_note")} Editable — the agent runs on this text.`;
  const photo = isImage
    ? `<img src="${s.artifact}" alt="damaged label"/>
       <div class="photo-note">A photograph — no machine-readable fields. The agent OCRs it (extract_from_document), or uses your override below.</div>`
    : "";

  art.innerHTML = photo +
    `<textarea class="artifact-edit${isImage ? " ocr" : ""}" id="artifactEdit" spellcheck="false"></textarea>
     <div class="artifact-foot"><span>${label}</span>
     <button class="reset-art" id="resetArt">reset to demo</button></div>`;
  const ta = $("#artifactEdit");
  ta.value = (key in EDITS) ? EDITS[key] : original;
  const reset = $("#resetArt");
  const sync = () => {
    EDITS[key] = ta.value;
    reset.style.display = (ta.value.trim() !== original.trim()) ? "inline" : "none";
  };
  ta.addEventListener("input", sync);
  reset.addEventListener("click", () => { ta.value = original; delete EDITS[key]; sync(); });
  sync();
  // reset result/trace (a fresh scenario starts clean)
  $("#resultPanel").innerHTML = "";
  resetTrace();
}

function resetTrace() {
  $("#trace").innerHTML = `<div class="trace-empty"><div class="mk">${icon("conveyor_belt")}</div>
    <b>Idle</b><span>Run a scenario to stream the agent's steps here.</span></div>`;
}

function skeletonCard(lines) {
  return `<div class="skeleton">${lines.map(w => `<div class="sk-line ${w}"></div>`).join("")}</div>`;
}
function showSkeletons() {
  $("#trace").innerHTML = skeletonCard(["w40", "w90", "w70"]) + skeletonCard(["w25", "w70"]) + skeletonCard(["w40", "w90"]);
  $("#resultPanel").innerHTML = `<div class="result-card"><div class="loader"><span class="dot"></span>Agent is working…</div>
    <div style="margin-top:12px">${["w90", "w70", "w40"].map(w => `<div class="sk-line ${w}"></div>`).join("")}</div></div>`;
}
function clearPlaceholders() {
  $("#trace").querySelectorAll(".trace-empty, .skeleton").forEach(x => x.remove());
}

/* word-by-word typewriter that preserves the rendered markup */
function typewriteInto(container, html) {
  container.innerHTML = html;
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const textNodes = []; while (walker.nextNode()) textNodes.push(walker.currentNode);
  const spans = [];
  textNodes.forEach(tn => {
    const frag = document.createDocumentFragment();
    tn.textContent.split(/(\s+)/).forEach(part => {
      if (part === "" ) return;
      if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); return; }
      const s = document.createElement("span"); s.className = "tw"; s.textContent = part;
      frag.appendChild(s); spans.push(s);
    });
    tn.parentNode.replaceChild(frag, tn);
  });
  const caret = el(`<span class="caret"></span>`);
  let i = 0;
  const tick = () => {
    if (i < spans.length) {
      spans[i].classList.add("on");
      spans[i].after(caret);
      i++;
      setTimeout(tick, 15);
    } else { caret.remove(); }
  };
  tick();
}

/* chips + rendering */
function provChip(p) {
  const cls = (p === "gemini" || p === "groq") ? `prov-${p}` : "";
  return `<span class="chip ${cls}">${icon("smart_toy")}${p}</span>`;
}
function diffTable(diff) {
  if (!diff || !diff.length) return `<p style="color:var(--muted);font-size:12px;margin-top:8px;">No fields changed.</p>`;
  const rows = diff.map(d => `<tr><td class="field">${esc(d.field)}</td>
    <td class="before">${esc(d.before)}</td><td class="after">${esc(d.after)}</td></tr>`).join("");
  return `<table class="diff"><tr><th>Field</th><th>Before</th><th>After</th></tr>${rows}</table>`;
}
function statusBadge(resolved, code) {
  if (code === "ESCALATED") return `<div class="badge escal">${icon("support_agent")}ESCALATED TO HUMAN</div>`;
  if (resolved) return `<div class="badge resolved">${icon("verified")}RESOLVED &amp; VERIFIED</div>`;
  return `<div class="badge open">${icon("pending")}UNRESOLVED</div>`;
}

function showNotice(n) {
  const trace = $("#trace");
  clearPlaceholders();
  const existing = trace.querySelector(".mode-banner");
  if (existing) existing.remove();
  const b = el(`<div class="mode-banner ${n.mode}">${icon(n.mode === "replay" ? "offline_bolt" : "bolt")}${esc(n.text)}</div>`);
  trace.prepend(b);
}

function renderStep(step) {
  // Suppress the raw "both providers failed" step — the server resets and
  // falls back to replay, so we never show the giant 429 payload.
  if (step.provider === "none") return;
  const trace = $("#trace");
  clearPlaceholders();
  const tool = step.tool, obs = step.observation, prov = step.provider || "?";
  if (!tool) {
    trace.appendChild(el(`<div class="step"><div class="step-hd">${icon("flag")}
      <span class="step-n">Step ${step.step}</span> · Final answer ${provChip(prov)}</div></div>`));
    trace.scrollTop = trace.scrollHeight; return;
  }
  let chips = provChip(prov);
  if (obs && typeof obs === "object") {
    if (obs.ok === true) chips += `<span class="chip ok">${icon("check")}ok</span>`;
    else if (obs.ok === false) chips += `<span class="chip fail">${icon("error")}${esc(obs.failure_class || "fail")}</span>`;
    const n = (obs.retry_log || []).length;
    if (n) chips += `<span class="chip retry">${icon("replay")}retried ×${n}</span>`;
    if (["nominatim", "mock_fallback", "cache_fallback", "gemini_vision"].includes(obs.source))
      chips += `<span class="chip">${icon("cloud")}${esc(obs.source)}</span>`;
    if (obs.idempotent_replay) chips += `<span class="chip">${icon("history")}idempotent replay</span>`;
    if (obs.require_approval) chips += `<span class="chip approval">${icon("gavel")}approval required</span>`;
  }
  const card = el(`<div class="step"><div class="step-hd">${icon(TOOL_ICON[tool] || "chevron_right")}
    <span class="step-n">Step ${step.step}</span> <span class="step-tool">${esc(tool)}</span> ${chips}</div>
    <div class="step-body"></div></div>`);
  const body = card.querySelector(".step-body");
  if (tool === "verify_shipment" && obs && typeof obs === "object") {
    body.innerHTML = statusBadge(obs.resolved, (obs.current_state || {}).exception_code) + diffTable(obs.diff);
  } else {
    const awaiting = (obs && obs.status === "awaiting_human_approval")
      ? `<div class="badge approval">${icon("gavel")}AWAITING HUMAN APPROVAL</div>` : "";
    body.innerHTML = awaiting + `<details class="io"><summary>input / observation</summary>
      <pre>${esc(JSON.stringify(step.tool_input || {}, null, 2))}
${esc(JSON.stringify(obs, null, 2))}</pre></details>`;
  }
  trace.appendChild(card);
  trace.scrollTop = trace.scrollHeight;
}

function mdToHtml(t) {
  return esc(t)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/^\s*(\d+)\.\s+(.*)$/gm, "<li>$2</li>")
    .replace(/(<li>[\s\S]*<\/li>)/, "<ol>$1</ol>")
    .replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>");
}

// Human-readable summary of what an action changed, for the audit.
function describeChanges(a) {
  if (!a.ok) return a.error || `${a.failure_class || "failed"} — no change applied`;
  const c = a.changed || {};
  const parts = Object.entries(c)
    .filter(([k]) => k !== "reason")
    .map(([k, v]) => {
      if (k === "exception_code" && (v === null || v === "")) return "exception cleared";
      if (k === "credit_usd") return `credit → $${v}`;
      return `${k} → ${v === null ? "—" : v}`;
    });
  return parts.join("  ·  ") || "state updated";
}

function renderResult(res) {
  const p = $("#resultPanel");
  const sd = res.state_diff;
  const trace = res.trace || [];
  const audit = (sd && sd.audit_log) || [];
  const code = sd ? (sd.current_state || {}).exception_code : null;
  const provider = trace.length ? trace[trace.length - 1].provider : "none";
  const mode = trace.some(s => s.provider === "replay") ? "replay" : "live";
  const retries = audit.reduce((n, a) => n + ((a.retry_log || []).length), 0);
  const dur = res._duration || (RUN_START ? ((Date.now() - RUN_START) / 1000).toFixed(1) + "s" : "—");
  p.innerHTML = "";

  // 1) DASHBOARD header: status + shipment + metric tiles
  if (sd) {
    const toolCalls = trace.filter(s => s.tool).length;
    const stats = [
      [toolCalls, "tool calls"], [audit.length, "state changes"], [retries, "retries"],
      [res.iterations, "LLM turns"], [dur, "duration"], [mode === "replay" ? "replay" : provider, "engine"],
    ];
    p.appendChild(el(`<div class="report-head">
      <div class="rh-top">${statusBadge(sd.resolved, code)}<span class="rh-ship">${esc(sd.shipment_id)}</span></div>
      <div class="stat-grid">${stats.map(([v, k]) =>
        `<div class="stat"><div class="v">${esc(v)}</div><div class="k">${k}</div></div>`).join("")}</div></div>`));
  }

  // 2) REPORT: the resolution narrative (typewriter)
  p.appendChild(el(`<div class="result-card"><div class="rc-hd">${icon("summarize")} Resolution report</div>
    <div class="answer-md" id="answerMd"></div></div>`));
  typewriteInto($("#answerMd"), mdToHtml(res.answer || "(no answer)"));

  if (sd) {
    // 3) Verified before/after diff
    p.appendChild(el(`<div class="result-card"><div class="rc-hd">${icon("difference")} Verified state diff</div>
      ${diffTable(sd.diff)}</div>`));

    // 4) Detailed, friendly action audit (what each action did)
    const rows = audit.map(a => {
      const st = a.ok ? `<span class="chip ok">${icon("check")}ok</span>`
        : `<span class="chip fail">${icon("error")}${esc(a.failure_class || "fail")}</span>`;
      const rl = (a.retry_log || []).length;
      const retry = rl ? `<span class="chip retry">${icon("replay")}${rl} retr${rl === 1 ? "y" : "ies"}</span>` : "";
      const att = `<span class="chip">${icon("repeat")}${a.attempts}×</span>`;
      return `<div class="audit-row"><div class="seq">${a.seq}</div>
        <div class="a-main"><div class="a-name">${icon(TOOL_ICON[a.tool] || "bolt")}${esc(a.tool)}</div>
        <div class="a-desc">${esc(describeChanges(a))}</div></div>
        <div class="a-chips">${st}${att}${retry}</div></div>`;
    }).join("");
    p.appendChild(el(`<div class="result-card"><div class="rc-hd">${icon("account_tree")} Action audit</div>
      <div>${rows || '<p style="color:var(--muted);font-size:12px">No state changes were needed.</p>'}</div></div>`));

    const human = code === "ESCALATED" ? "REQUIRED — escalated to a human operator"
      : (sd.resolved ? "not required — resolved &amp; verified" : "pending");
    p.appendChild(el(`<div class="foot-note">further action / human intervention: <b style="color:var(--text)">${human}</b></div>`));
  }
  p.appendChild(el(`<div class="foot-note">engine: ${mode} (${provider}) · live actions ${$("#liveToggle").checked ? "on" : "off"}</div>`));
}

function run() {
  if (running) return;
  running = true;
  RUN_START = Date.now();
  const btn = $("#runBtn");
  btn.disabled = true; btn.style.opacity = ".6";
  showSkeletons();

  const live = $("#liveToggle").checked ? 1 : 0;
  // If the editable artifact was changed, run on the CUSTOM text (live planner
  // only); if untouched, run the built-in scenario (which can replay-fallback).
  const s = META.scenarios[ACTIVE];
  const original = (s.artifact_kind === "image") ? (s.ocr_text || "") : s.artifact;
  const ta = document.querySelector("#artifactEdit");
  let extra = "";
  // Edited → run on the custom text (source_type carries the modality; for the
  // photo that's ocr_text, so an edit skips vision and parses the text directly).
  if (ta && ta.value.trim() && ta.value.trim() !== original.trim()) {
    extra = `&raw_input=${encodeURIComponent(ta.value)}&source_type=${encodeURIComponent(s.source_type)}`;
  }
  const es = new EventSource(`/api/run?scenario=${ACTIVE}&live=${live}${extra}`);
  es.addEventListener("notice", e => showNotice(JSON.parse(e.data)));
  es.addEventListener("reset", () => { $("#trace").innerHTML = ""; });
  es.addEventListener("step", e => renderStep(JSON.parse(e.data)));
  es.addEventListener("result", e => {
    const r = JSON.parse(e.data);
    r._duration = RUN_START ? ((Date.now() - RUN_START) / 1000).toFixed(1) + "s" : "—";
    r._scenario = ACTIVE;
    LAST_RUN = r;
    renderResult(r);
    if (r.state_diff) { saveRunToHistory(r); $("#exportBtn").hidden = false; }
  });
  es.addEventListener("agent_error", e => {
    $("#resultPanel").innerHTML = `<div class="result-card"><div class="rc-hd">${icon("error")} Error</div>
      <div class="answer-md">${esc(JSON.parse(e.data).error)}</div></div>`;
  });
  const finish = () => { es.close(); running = false; btn.disabled = false; btn.style.opacity = "1"; };
  es.addEventListener("done", finish);
  es.onerror = () => { if (es.readyState === EventSource.CLOSED) finish(); };
}

/* ---------------- theme ---------------- */
const THEME_KEY = "lodestar-theme";
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  const ic = $("#themeToggle .material-symbols-rounded");
  if (ic) ic.textContent = t === "light" ? "dark_mode" : "light_mode"; // show what you'll switch TO
}
let theme = (() => { try { return localStorage.getItem(THEME_KEY) || "light"; } catch (e) { return "light"; } })();
applyTheme(theme);
$("#themeToggle").addEventListener("click", () => {
  theme = theme === "light" ? "dark" : "light";
  try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
  applyTheme(theme);
});

/* ==========================================================================
   OPERATOR PLATFORM — Shipments · History · Insights (+ notes, export, pin)
   ========================================================================== */
let SHIPMENTS = null, SHIP_FILTER = "all", LAST_RUN = null;
const HIST_KEY = "lodestar-history", NOTES_KEY = "lodestar-notes";
const jget = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
const jset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
const loadHist = () => jget(HIST_KEY, []);
const saveHist = (h) => { jset(HIST_KEY, h.slice(0, 50)); updateHistCount(); };
const codeOf = (r) => ((r.res.state_diff || {}).current_state || {}).exception_code;
const outcomeOf = (r) => codeOf(r) === "ESCALATED" ? "escalated"
  : ((r.res.state_diff || {}).resolved ? "resolved" : "open");

function updateHistCount() {
  const n = loadHist().length;
  ["#histCount", "#histCount2"].forEach(s => { const e = $(s); if (e) e.textContent = n ? n : ""; });
}

function showView(v) {
  document.querySelectorAll(".op-tab").forEach(t => t.classList.toggle("active", t.dataset.view === v));
  document.querySelectorAll(".view").forEach(x => x.hidden = x.id !== "view-" + v);
  if (v === "shipments") renderShipments();
  if (v === "history") renderHistory();
  if (v === "insights") renderInsights();
}

/* ---- Shipments explorer ---- */
function statusBadgeSmall(s) {
  if (s.exception_code === "ESCALATED") return `<span class="chip escal-chip">${icon("support_agent")}escalated</span>`;
  if (s.exception_code) return `<span class="chip fail">${icon("warning")}${esc(s.exception_code)}</span>`;
  return `<span class="chip ok">${icon("check")}${esc(s.status)}</span>`;
}
async function renderShipments() {
  const grid = $("#shipGrid");
  if (!SHIPMENTS) {
    grid.innerHTML = `<div class="loader"><span class="dot"></span>Loading shipments…</div>`;
    try { SHIPMENTS = (await (await fetch("/api/shipments")).json()).shipments; }
    catch (e) { grid.innerHTML = "Failed to load."; return; }
    buildShipFilter();
  }
  const q = ($("#shipSearch").value || "").toLowerCase();
  const notes = jget(NOTES_KEY, {});
  const items = SHIPMENTS.filter(s => {
    const hay = `${s.id} ${s.carrier} ${s.destination} ${s.customer} ${s.exception_code || ""}`.toLowerCase();
    const codeGroup = s.exception_code || "clear";
    return hay.includes(q) && (SHIP_FILTER === "all" || codeGroup === SHIP_FILTER);
  });
  grid.innerHTML = items.map(s => `
    <div class="ship-card" data-id="${s.id}">
      <div class="sc-top"><span class="sc-id">${esc(s.id)}</span>${statusBadgeSmall(s)}</div>
      <div class="sc-note">${esc(s.exception_note || "No active exception")}</div>
      <div class="sc-meta">
        <span>${icon("local_shipping")} ${esc(s.carrier)}</span>
        <span>${icon("place")} ${esc((s.destination || "").split(",")[0])}</span>
        <span>${icon("payments")} $${esc(s.order_value_usd)}</span>
        <span>${icon("person")} ${esc(s.customer)}</span>
      </div>
      <textarea class="sc-noteinput" data-id="${s.id}" placeholder="Add an operator note…">${esc(notes[s.id] || "")}</textarea>
      <div class="sc-actions">
        ${s.scenario ? `<button class="btn btn-solid sc-run" data-scenario="${s.scenario}">Run agent ${icon("bolt")}</button>` : ""}
      </div>
    </div>`).join("") || `<div class="empty-mini">No shipments match.</div>`;

  grid.querySelectorAll(".sc-run").forEach(b => b.addEventListener("click", () => {
    showView("console"); setScenario(b.dataset.scenario);
    $("#view-console").scrollIntoView({ behavior: "smooth" });
    setTimeout(() => run(), 400);
  }));
  grid.querySelectorAll(".sc-noteinput").forEach(t => t.addEventListener("change", () => {
    const n = jget(NOTES_KEY, {}); n[t.dataset.id] = t.value; jset(NOTES_KEY, n);
  }));
}
function buildShipFilter() {
  const codes = ["all", ...new Set(SHIPMENTS.map(s => s.exception_code || "clear"))];
  $("#shipFilter").innerHTML = codes.map(c =>
    `<button class="fchip${c === SHIP_FILTER ? " active" : ""}" data-c="${c}">${c === "all" ? "All" : esc(c)}</button>`).join("");
  $("#shipFilter").querySelectorAll(".fchip").forEach(b => b.addEventListener("click", () => {
    SHIP_FILTER = b.dataset.c; buildShipFilter(); renderShipments();
  }));
}

/* ---- Run history ---- */
function saveRunToHistory(r) {
  const trace = (r.trace || []).map(s => ({ tool: s.tool, provider: s.provider }));
  const h = loadHist();
  h.unshift({
    id: "run-" + (r.state_diff.shipment_id) + "-" + Date.now(), ts: Date.now(), pinned: false,
    scenario: r._scenario,
    res: { answer: r.answer, iterations: r.iterations, trace, state_diff: r.state_diff, _duration: r._duration },
  });
  saveHist(h);
}
function renderHistory() {
  const list = $("#histList");
  let h = loadHist();
  if (!h.length) { list.innerHTML = `<div class="empty-mini">${icon("history")} No runs yet — run the agent and they'll appear here.</div>`; return; }
  h = [...h].sort((a, b) => (b.pinned - a.pinned) || (b.ts - a.ts));
  list.innerHTML = h.map(r => {
    const sd = r.res.state_diff || {}; const out = outcomeOf(r);
    const badge = out === "escalated" ? `<span class="chip escal-chip">${icon("support_agent")}escalated</span>`
      : out === "resolved" ? `<span class="chip ok">${icon("verified")}resolved</span>`
        : `<span class="chip fail">${icon("pending")}open</span>`;
    const t = new Date(r.ts).toLocaleString();
    const tools = (r.res.trace || []).filter(s => s.tool).length;
    return `<div class="hist-row" data-id="${r.id}">
      <div class="hr-main">
        <div class="hr-top">${badge}<span class="rh-ship">${esc(sd.shipment_id || "?")}</span>
          <span class="hr-scn">scenario ${esc(r.scenario || "custom")}</span></div>
        <div class="hr-sub">${t} · ${tools} tool calls · ${esc(r.res._duration || "")}</div>
      </div>
      <div class="hr-actions">
        <button class="icon-btn open" title="Open">${icon("visibility")}</button>
        <button class="icon-btn pin${r.pinned ? " on" : ""}" title="Pin">${icon(r.pinned ? "star" : "star_outline")}</button>
        <button class="icon-btn exp" title="Export">${icon("download")}</button>
        <button class="icon-btn del danger" title="Delete">${icon("delete")}</button>
      </div></div>`;
  }).join("");
  const byId = (id) => loadHist().find(x => x.id === id);
  list.querySelectorAll(".hist-row").forEach(row => {
    const id = row.dataset.id;
    row.querySelector(".open").addEventListener("click", () => {
      const r = byId(id); if (!r) return; showView("console"); renderResult(r.res);
      $("#exportBtn").hidden = false; LAST_RUN = r.res; $("#view-console").scrollIntoView({ behavior: "smooth" });
    });
    row.querySelector(".pin").addEventListener("click", () => {
      const h2 = loadHist(); const it = h2.find(x => x.id === id); if (it) it.pinned = !it.pinned; saveHist(h2); renderHistory();
    });
    row.querySelector(".del").addEventListener("click", () => { saveHist(loadHist().filter(x => x.id !== id)); renderHistory(); });
    row.querySelector(".exp").addEventListener("click", () => exportRun(byId(id).res));
  });
}
function exportRun(res) {
  const sd = res.state_diff || {};
  const report = {
    product: "LODESTAR", shipment: sd.shipment_id, resolved: sd.resolved,
    status: (sd.current_state || {}).exception_code || "RESOLVED",
    duration: res._duration, iterations: res.iterations,
    summary: res.answer, diff: sd.diff, audit: sd.audit_log,
    exported_at: new Date().toISOString(),
  };
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = `lodestar-${sd.shipment_id || "run"}.json`;
  a.click(); URL.revokeObjectURL(a.href);
}

/* ---- Insights (analytics) ---- */
function bar(label, val, max, cls) {
  const pct = max ? Math.round((val / max) * 100) : 0;
  return `<div class="bar-row"><div class="bar-lab">${esc(label)}</div>
    <div class="bar-track"><div class="bar-fill ${cls || ""}" style="width:${pct}%"></div></div>
    <div class="bar-val">${val}</div></div>`;
}
function renderInsights() {
  const box = $("#insights"); const h = loadHist();
  if (!h.length) { box.innerHTML = `<div class="empty-mini">${icon("monitoring")} No data yet — run a few scenarios to populate insights.</div>`; return; }
  const runs = h.length;
  const resolved = h.filter(r => outcomeOf(r) === "resolved").length;
  const escalated = h.filter(r => outcomeOf(r) === "escalated").length;
  const avgTools = (h.reduce((n, r) => n + (r.res.trace || []).filter(s => s.tool).length, 0) / runs).toFixed(1);
  const avgDur = (h.reduce((n, r) => n + parseFloat(r.res._duration || 0), 0) / runs).toFixed(1) + "s";
  const stats = [[runs, "total runs"], [Math.round(resolved / runs * 100) + "%", "resolved"],
    [Math.round(escalated / runs * 100) + "%", "escalated"], [avgTools, "avg tool calls"], [avgDur, "avg duration"]];
  // tool usage frequency
  const freq = {};
  h.forEach(r => (r.res.trace || []).forEach(s => { if (s.tool) freq[s.tool] = (freq[s.tool] || 0) + 1; }));
  const top = Object.entries(freq).sort((a, b) => b[1] - a[1]);
  const maxF = top.length ? top[0][1] : 1;
  // outcomes
  const oc = { resolved, escalated, open: runs - resolved - escalated };
  const maxO = Math.max(1, ...Object.values(oc));
  box.innerHTML = `
    <div class="report-head"><div class="rh-top">${icon("monitoring")} <b>Operations insights</b></div>
      <div class="stat-grid">${stats.map(([v, k]) => `<div class="stat"><div class="v">${esc(v)}</div><div class="k">${k}</div></div>`).join("")}</div></div>
    <div class="ins-grid">
      <div class="result-card"><div class="rc-hd">${icon("bar_chart")} Tool usage</div>
        ${top.map(([t, c]) => bar(t, c, maxF, "")).join("") || "<p>—</p>"}</div>
      <div class="result-card"><div class="rc-hd">${icon("donut_small")} Outcomes</div>
        ${bar("resolved", oc.resolved, maxO, "g")}${bar("escalated", oc.escalated, maxO, "a")}${bar("open", oc.open, maxO, "r")}</div>
    </div>`;
}

/* ---------------- boot ---------------- */
document.documentElement.classList.remove("no-js");
$("#liveToggle").addEventListener("change", (e) =>
  $("#liveHint").textContent = e.target.checked ? "REAL sends" : "simulated");
$("#runBtn").addEventListener("click", run);
$("#exportBtn").addEventListener("click", () => LAST_RUN && exportRun(LAST_RUN));
document.querySelectorAll(".op-tab").forEach(t => t.addEventListener("click", () => showView(t.dataset.view)));
$("#shipSearch") && $("#shipSearch").addEventListener("input", renderShipments);
$("#clearHist") && $("#clearHist").addEventListener("click", () => { saveHist([]); renderHistory(); });
updateHistCount();
loadMeta();
initReveals();
initAnim();
