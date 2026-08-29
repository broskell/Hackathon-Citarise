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

let META = null, ACTIVE = "A", running = false;

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

function renderResult(res) {
  const p = $("#resultPanel");
  p.innerHTML = `<div class="result-card"><div class="rc-hd">${icon("check_circle")} Resolution</div>
    <div class="answer-md" id="answerMd"></div></div>`;
  typewriteInto($("#answerMd"), mdToHtml(res.answer || "(no answer)"));
  const sd = res.state_diff;
  if (sd) {
    const code = (sd.current_state || {}).exception_code;
    p.appendChild(el(`<div class="result-card"><div class="rc-hd">${icon("difference")}
      Verified state diff · <span class="step-tool">${esc(sd.shipment_id)}</span></div>
      ${statusBadge(sd.resolved, code)}${diffTable(sd.diff)}</div>`));
    const rows = (sd.audit_log || []).map(a => {
      const st = a.ok ? `<span class="chip ok">${icon("check")}ok</span>`
                      : `<span class="chip fail">${icon("error")}${esc(a.failure_class || "fail")}</span>`;
      const rl = (a.retry_log || []).length;
      const retry = rl ? `<span class="chip retry">${icon("replay")}${rl} retr${rl === 1 ? "y" : "ies"}</span>` : "";
      return `<div class="tl-row"><div class="seq">${a.seq}</div><span class="nm">${esc(a.tool)}</span>
        ${st}<span class="chip">${icon("repeat")}${a.attempts}×</span>${retry}</div>`;
    }).join("");
    if (rows) p.appendChild(el(`<div class="result-card"><div class="rc-hd">${icon("account_tree")}
      Resilient-execution audit</div><div>${rows}</div></div>`));
  }
  if (sd) {
    const code = (sd.current_state || {}).exception_code;
    const human = code === "ESCALATED" ? "REQUIRED — escalated to a human operator"
      : (sd.resolved ? "not required — resolved &amp; verified" : "pending");
    p.appendChild(el(`<div class="foot-note">further action / human intervention: <b style="color:var(--text)">${human}</b></div>`));
  }
  const prov = (res.trace && res.trace.length) ? res.trace[res.trace.length - 1].provider : "none";
  p.appendChild(el(`<div class="foot-note">final provider: ${prov} · ${res.iterations} iterations · live actions ${$("#liveToggle").checked ? "on" : "off"}</div>`));
}

function run() {
  if (running) return;
  running = true;
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
  es.addEventListener("result", e => renderResult(JSON.parse(e.data)));
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

/* ---------------- boot ---------------- */
document.documentElement.classList.remove("no-js");
$("#liveToggle").addEventListener("change", (e) =>
  $("#liveHint").textContent = e.target.checked ? "REAL sends" : "simulated");
$("#runBtn").addEventListener("click", run);
loadMeta();
initReveals();
initAnim();
