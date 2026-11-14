/* RecallOps frontend: API wiring + motion (GSAP/ScrollTrigger/Lenis). */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = (s) => DOMPurify.sanitize(marked.parse(s || ""));
const REDUCE = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const HAS_GSAP = typeof window.gsap !== "undefined";

// ---------- smooth scroll + reveals ----------
let lenis = null;
if (HAS_GSAP) gsap.registerPlugin(ScrollTrigger);
if (!REDUCE && typeof window.Lenis !== "undefined") {
  lenis = new Lenis({ lerp: 0.09, smoothWheel: true });
  if (HAS_GSAP) { lenis.on("scroll", ScrollTrigger.update); gsap.ticker.add((t) => lenis.raf(t * 1000)); gsap.ticker.lagSmoothing(0); }
  else { const raf = (t) => { lenis.raf(t); requestAnimationFrame(raf); }; requestAnimationFrame(raf); }
  document.querySelectorAll('a[href^="#"]').forEach((a) => a.addEventListener("click", (e) => {
    const el = document.querySelector(a.getAttribute("href")); if (el) { e.preventDefault(); lenis.scrollTo(el, { offset: -60 }); }
  }));
}
if (HAS_GSAP && !REDUCE) {
  gsap.from(".hero-title .line", { yPercent: 110, opacity: 0, duration: 1.2, ease: "expo.out", stagger: 0.12, delay: 0.15 });
  gsap.utils.toArray(".reveal").forEach((el) => {
    gsap.from(el, { y: 36, opacity: 0, duration: 1, ease: "power3.out", scrollTrigger: { trigger: el, start: "top 88%" } });
  });
  gsap.utils.toArray(".kicker").forEach((el) => gsap.from(el, { opacity: 0, x: -20, duration: 0.8, scrollTrigger: { trigger: el, start: "top 90%" } }));
  gsap.from(".dnode", { scale: 0.4, opacity: 0, transformOrigin: "center", duration: 0.9, ease: "back.out(1.7)", stagger: 0.12, scrollTrigger: { trigger: "#how svg", start: "top 80%" } });
}

// ---------- scroll depth (drives the background veil only) ----------
function onScroll() {
  const max = document.documentElement.scrollHeight - window.innerHeight;
  const f = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
  document.documentElement.style.setProperty("--depth", f.toFixed(3));
}
window.addEventListener("scroll", onScroll, { passive: true }); onScroll();

// ---------- UI helpers ----------
const LOAD_MSGS_ON = ["descending into incident memory…", "recall(): semantic + keyword + graph + temporal…", "reranking past incidents…", "reflect(): reasoning like a senior SRE…", "grounding the plan in what worked…"];
const LOAD_MSGS_OFF = ["thinking without memory…", "drafting a generic checklist…"];
function sonar(el, msgs, sub = "") {
  el.classList.remove("muted");
  el.innerHTML = `<div class="sonar"><div class="ring"><span></span><i></i></div><div><div class="msg">${esc(msgs[0])}</div><div class="sub">${esc(sub)}<span class="secs">0.0s</span></div></div></div>`;
  const msg = el.querySelector(".msg"), secs = el.querySelector(".secs"), t0 = performance.now();
  let i = 0;
  const iv = setInterval(() => {
    if (!el.contains(msg)) return clearInterval(iv);
    secs.textContent = `${((performance.now() - t0) / 1000).toFixed(1)}s`;
    if (Math.round((performance.now() - t0) / 100) % 28 === 0) { i = (i + 1) % msgs.length; msg.textContent = msgs[i]; }
  }, 100);
  return () => clearInterval(iv);
}
const errBox = (m) => `<div class="err">${esc(m)}</div>`;
const warnBox = (ws) => (ws || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("");
function streamIn(el, html) {
  el.classList.remove("muted");
  el.innerHTML = html;
  if (!HAS_GSAP || REDUCE) return;
  const kids = el.querySelectorAll(":scope > *, :scope li");
  gsap.from(kids, { opacity: 0, y: 10, filter: "blur(4px)", duration: 0.45, ease: "power2.out", stagger: { each: 0.05, amount: 1.6 } });
}
const CONF = { low: [0.22, "#6b6b6b"], medium: [0.58, "#b3b3b3"], high: [0.92, "#ffffff"] };
function meter(el, conf) {
  const [v, color] = CONF[conf] || [0, "#727272"];
  const L = 125.7; // half-circle length for r=40
  el.innerHTML = `<svg viewBox="0 0 100 62" aria-label="confidence ${esc(conf || "unknown")}"><path class="track" d="M10 52 A40 40 0 0 1 90 52" fill="none" stroke-width="7"/><path class="arc" d="M10 52 A40 40 0 0 1 90 52" fill="none" stroke-width="7" stroke="${color}" style="color:${color}" stroke-dasharray="${L}" stroke-dashoffset="${L}"/><text x="50" y="50" text-anchor="middle" class="mlabel">${esc(conf || "—")}</text></svg>`;
  const arc = el.querySelector(".arc"), off = L * (1 - v);
  if (HAS_GSAP && !REDUCE) gsap.to(arc, { strokeDashoffset: off, duration: 1.4, ease: "power3.out" });
  else arc.setAttribute("stroke-dashoffset", off);
}
const tyChip = (t) => `<span class="ty ${esc(t)}">${esc(t)}</span>`;
const memItem = (m) => `<li><div class="meta">${tyChip(m.type)}<span>${esc((m.occurred || "").slice(0, 10))}</span><span class="src">${esc(m.source || "consolidated")}</span></div>${esc(m.text)}</li>`;

async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, { headers: { "content-type": "application/json" }, ...opts }); }
  catch { throw new Error("Network error: could not reach the RecallOps API. Check your connection and retry."); }
  let body = null; try { body = await r.json(); } catch { /* not json */ }
  if (!r.ok) {
    let d = body && body.detail; if (Array.isArray(d)) d = d.map((x) => x.msg).join("; ");
    if (r.status === 504) d = "The memory dive took too long (timeout). Try again, or untick reflect for a faster run.";
    throw new Error(d || `Request failed (${r.status}). Please try again.`);
  }
  return body;
}

// ---------- status + examples ----------
const LABELS = ["💳 payments-api 5xx after deploy", "🛒 empty carts · Redis evictions", "🗄️ orders DB pool timeouts", "🔒 mobile SSL handshake failures"];
api("/api/health").then((h) => {
  h.examples.forEach((ex, i) => {
    const b = document.createElement("button");
    b.className = "chip"; b.textContent = LABELS[i] || ex.slice(0, 40); b.title = ex;
    b.onclick = () => { $("alert").value = ex; countAlert(); runTriage(); };
    $("examples").appendChild(b);
  });
  if (!$("alert").value) { $("alert").value = h.examples[0]; countAlert(); }
}).catch(() => { $("examples").textContent = "API unavailable right now."; });
const countAlert = () => { $("alertCount").textContent = `${$("alert").value.length}/500`; };
$("alert").addEventListener("input", countAlert);

// ---------- triage ----------
function renderNodes(res) {
  $("recallCount").textContent = `${res.memories.length} memories · ${res.sources.length} incidents`;
  $("nodes").innerHTML = res.sources.length
    ? res.sources.map((s, i) => `<span class="node ${i % 3 === 2 ? "violet" : ""}">${esc(s)}</span>`).join("")
    : `<span class="muted text-sm">No past incidents surfaced.</span>`;
  if (HAS_GSAP && !REDUCE) gsap.from("#nodes .node", { scale: 0, opacity: 0, duration: 0.6, ease: "back.out(2)", stagger: 0.07 });
  $("recalled").innerHTML = res.memories.slice(0, 40).map(memItem).join("");
  if (res.reflection) streamIn($("reflect"), md(res.reflection));
  else { $("reflect").classList.add("muted"); $("reflect").textContent = $("useReflect").checked ? "No reflect output." : "Reflect was turned off for this run."; }
  window.dispatchEvent(new Event("recall-pulse"));
}
async function runTriage() {
  const alert = $("alert").value.trim();
  if (!alert) { $("onOut").innerHTML = errBox("Type or pick an alert first."); return; }
  $("runTriage").disabled = true;
  const reflect = $("useReflect").checked;
  $("offMeter").innerHTML = $("onMeter").innerHTML = "";
  $("nodes").innerHTML = ""; $("recalled").innerHTML = ""; $("recallCount").textContent = "";
  const stopOff = sonar($("offOut"), LOAD_MSGS_OFF, "no memory · ");
  const stopOn = sonar($("onOut"), LOAD_MSGS_ON, reflect ? "recall + reflect · " : "recall · ");
  $("reflect").classList.add("muted"); $("reflect").textContent = reflect ? "Waiting for reflect…" : "Reflect is off for this run.";
  const off = api("/api/triage", { method: "POST", body: JSON.stringify({ alert, use_memory: false, reflect: false }) })
    .then((r) => { stopOff(); streamIn($("offOut"), warnBox(r.warnings) + md(r.plan)); meter($("offMeter"), r.confidence); })
    .catch((e) => { stopOff(); $("offOut").innerHTML = errBox(e.message); });
  const on = api("/api/triage", { method: "POST", body: JSON.stringify({ alert, use_memory: true, reflect }) })
    .then((r) => { stopOn(); streamIn($("onOut"), warnBox(r.warnings) + md(r.plan)); meter($("onMeter"), r.confidence); renderNodes(r); })
    .catch((e) => { stopOn(); $("onOut").innerHTML = errBox(e.message); $("reflect").textContent = ""; });
  await Promise.allSettled([off, on]);
  $("runTriage").disabled = false;
}
$("runTriage").onclick = runTriage;

// ---------- learning curve ----------
const LC = {
  alert: "inventory-sync: oversell alerts firing, stock counts drifting from the warehouse system after the 02:00 feed import",
  pm: {
    title: "Oversells after warehouse feed import (inventory-sync)", service: "inventory-sync", severity: "SEV2",
    symptoms: "Oversell alerts on 340 SKUs right after the 02:00 warehouse feed import; stock_available negative for fast movers; inventory-sync logs 'duplicate delta applied' warnings.",
    root_cause: "The warehouse vendor started re-sending the previous night's delta file when their SFTP upload retried, and inventory-sync applied the same deltas twice because imports were not idempotent.",
    what_worked: "Paused the import cron, replayed the feed from the last good checkpoint with idempotency keys (file checksum + line number), and re-synced stock from the WMS snapshot API. Added a checksum guard that rejects already-applied files.",
    what_failed: "Restarting inventory-sync pods did nothing. Manually correcting SKU counts in the admin panel was overwritten by the next delta.",
    teams: "inventory, data-platform", runbook: "RB-INV-02 feed replay", time_to_resolve_min: 52,
  },
};
$("lcAlert").textContent = LC.alert;
$("lcPm").textContent = Object.entries(LC.pm).map(([k, v]) => `${k}: ${v}`).join("\n");
let sandbox = null;
const setStep = (n) => ["s1", "s2", "s3"].forEach((id, i) => { $(id).classList.toggle("active", i === n - 1); $(id).classList.toggle("done", i < n - 1); });
setStep(1);
async function lcTriage(btn, out, info, meterEl) {
  btn.disabled = true;
  const stop = sonar(out, ["opening your private memory bank…", "recall()…", "planning…"], "sandbox · ");
  try {
    if (!sandbox) sandbox = (await api("/api/learning/start", { method: "POST" })).sandbox;
    const r = await api("/api/learning/triage", { method: "POST", body: JSON.stringify({ sandbox, alert: LC.alert }) });
    stop(); streamIn(out, warnBox(r.warnings) + md(r.plan)); meter(meterEl, r.confidence);
    info.innerHTML = `${r.memories.length} memories · ${r.seconds}s${r.sources.length ? ` · <span class="src">${esc(r.sources.join(", "))}</span>` : ""}`;
    if (r.memories.length) window.dispatchEvent(new Event("recall-pulse"));
    return r;
  } catch (e) { stop(); out.innerHTML = errBox(e.message); btn.disabled = false; return null; }
}
$("lc1").onclick = async () => { const r = await lcTriage($("lc1"), $("lcBefore"), $("lc1b"), $("lcMeter1")); if (r) { $("lc2").disabled = false; setStep(2); } };
$("lc2").onclick = async () => {
  $("lc2").disabled = true; $("lc2b").textContent = "retaining into Hindsight…";
  try {
    const r = await api("/api/learning/resolve", { method: "POST", body: JSON.stringify({ ...LC.pm, sandbox }) });
    $("lc2b").innerHTML = `retained <span class="src">${esc(r.incident_id)}</span>`; $("lc3").disabled = false; setStep(3);
    window.dispatchEvent(new Event("recall-pulse"));
  } catch (e) { $("lc2b").innerHTML = errBox(e.message); $("lc2").disabled = false; }
};
$("lc3").onclick = async () => { const r = await lcTriage($("lc3"), $("lcAfter"), $("lc3b"), $("lcMeter2")); $("lc3").disabled = false; if (r) { $("s3").classList.add("done"); } };

// ---------- resolve ----------
$("resForm").onsubmit = async (ev) => {
  ev.preventDefault();
  const fd = Object.fromEntries(new FormData($("resForm")).entries());
  fd.time_to_resolve_min = fd.time_to_resolve_min ? parseInt(fd.time_to_resolve_min, 10) : null;
  $("resBtn").disabled = true;
  const stop = sonar($("resOut"), ["sinking the postmortem into memory…", "retain(): extracting facts and entities…"], "");
  try {
    const r = await api("/api/resolve", { method: "POST", body: JSON.stringify(fd) });
    stop();
    $("resOut").innerHTML = `<div class="ok">Retained <b class="font-mono">${esc(r.incident_id)}</b>. Triage a similar alert above and watch it surface.</div>
      <details class="mt-2"><summary class="cursor-pointer text-neutral-400 text-sm">What was sent to Hindsight retain()</summary><pre class="pm">${esc(r.memory_text)}\n\ntags: ${esc(r.tags.join(", "))}\ntimestamp: ${esc(r.timestamp)}</pre></details>`;
    $("resForm").reset(); window.dispatchEvent(new Event("recall-pulse"));
  } catch (e) { stop(); $("resOut").innerHTML = errBox(e.message); }
  $("resBtn").disabled = false;
};

// ---------- explorer ----------
async function explore() {
  const q = $("exQ").value.trim();
  $("timeline").innerHTML = `<li style="list-style:none" class="-ml-6"></li>`;
  const stop = sonar($("timeline").firstChild, [q ? "recalling…" : "surfacing the latest memories…"], "");
  try {
    const r = await api(`/api/explore?q=${encodeURIComponent(q)}&limit=60`);
    stop();
    const rows = [...r.memories]; if (!q) rows.sort((a, b) => (b.occurred || "").localeCompare(a.occurred || ""));
    $("timeline").innerHTML = rows.map((m) => `<li class="${esc(m.type)}"><div class="meta">${tyChip(m.type)}<span>${esc((m.occurred || "").slice(0, 10))}</span><span class="src">${esc(m.source || "consolidated")}</span></div><div class="tx">${esc(m.text)}</div></li>`).join("") || `<li class="muted">No memories found.</li>`;
    if (HAS_GSAP && !REDUCE) gsap.from("#timeline li", { opacity: 0, x: -14, duration: 0.5, stagger: 0.03, ease: "power2.out" });
  } catch (e) { stop(); $("timeline").innerHTML = `<li>${errBox(e.message)}</li>`; }
}
$("exBtn").onclick = explore;
$("exQ").addEventListener("keydown", (e) => { if (e.key === "Enter") explore(); });
// Load the explorer when it scrolls into view (saves an API call for bouncing visitors).
new IntersectionObserver((ents, obs) => { if (ents.some((e) => e.isIntersecting)) { obs.disconnect(); explore(); } }, { rootMargin: "300px" }).observe($("explorer"));


// ---------- Evidence-driven incident workflow ----------
(() => {
  if (!$("wfRun")) return;
  const STEPS = ["investigate", "recall", "recommend", "approve", "simulate", "verify", "retain", "learn"];
  const KEY = "recallops-workflow-v1";
  let W = { alert: "", cmp: null, actions: {}, retained: null, sample: null, pm: null, learn: null };
  let samples = [];
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(W)); } catch {} };
  const status = (t) => ($("wfStatus").textContent = t || "");
  const post = (url, body) => api(url, { method: "POST", body: JSON.stringify(body) });
  const li = (xs) => (xs && xs.length ? `<ul class="wf-list">${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<div class="muted">None</div>');

  function stepper() {
    const st = Object.values(W.actions);
    const done = {
      investigate: !!W.cmp, recall: !!W.cmp && W.cmp.on.historical_incidents.length > 0, recommend: !!W.cmp,
      approve: st.some((a) => ["approved", "simulated", "verified_passed", "verified_failed"].includes(a.status)),
      simulate: st.some((a) => ["simulated", "verified_passed", "verified_failed"].includes(a.status)),
      verify: st.some((a) => a.status === "verified_passed"), retain: !!(W.retained && W.retained.retained),
      learn: !!(W.learn && W.learn.check && W.learn.check.found),
    };
    $("wfStepper").innerHTML = STEPS.map((s) => `<li class="${done[s] ? "on" : ""}">${done[s] ? "✓ " : ""}${s}</li>`).join("");
  }

  function invCard(r) {
    const i = r.investigation;
    const causes = (i.potential_root_causes || []).map((c) => `<li>${esc(c.cause)} <span class="badge ${c.basis === "evidence" ? "solid" : "muted"}">${esc(c.basis)}</span>${(c.incident_ids || []).map((x) => `<span class="badge">${esc(x)}</span>`).join("")}</li>`).join("");
    const ev = (i.supporting_evidence || []).map((e) => `<li>${esc(e.claim)} <span class="badge">${esc(e.incident_id)}</span></li>`).join("");
    const pf = (i.previous_failures || []).map((e) => `<li>${esc(e.attempt)} <span class="badge">${esc(e.incident_id)}</span></li>`).join("");
    return `${warnBox(r.warnings)}${warnBox(i.validation_notes)}
      <div class="flex flex-wrap gap-1 mb-1"><span class="badge">${r.llm_used ? "Groq LLM" : "deterministic (no LLM)"}</span><span class="badge">memory: ${esc(r.memory_backend)}</span><span class="badge">recall called: ${r.memory_recall_called ? "yes" : "no"}</span><span class="badge">${r.memories_returned} memories</span><span class="badge muted">${r.seconds}s</span></div>
      <div class="wf-k">Incident summary</div><div>${esc(i.summary)}</div>
      <div class="wf-k">Affected service</div><div>${esc(i.service || "unknown")}</div>
      <div class="wf-k">Observed symptoms</div>${li(i.symptoms)}
      <div class="wf-k">Potential root cause</div>${causes ? `<ul class="wf-list">${causes}</ul>` : '<div class="muted">Unknown</div>'}
      <div class="wf-k">Supporting evidence (from memory)</div>${ev ? `<ul class="wf-list">${ev}</ul>` : '<div class="muted">No historical evidence.</div>'}
      <div class="wf-k">Previous failed attempts</div>${pf ? `<ul class="wf-list">${pf}</ul>` : '<div class="muted">None known.</div>'}
      <div class="wf-k">Missing information</div>${li(i.missing_information)}
      <div class="wf-k">Historical incidents retrieved</div><div>${r.historical_incidents.map((h) => `<span class="badge">${esc(h.id)}</span>`).join("") || '<span class="muted">None</span>'}</div>
      <div class="wf-k">Next investigation steps</div>${li(i.next_steps)}`;
  }

  function simCard(h, hl) {
    return `<div class="wf-card ${hl ? "hl" : ""}"><div class="flex flex-wrap gap-1"><span class="badge solid">${esc(h.id)}</span>${h.service ? `<span class="badge">${esc(h.service)}</span>` : ""}<span class="badge">outcome: ${esc(h.outcome)}</span>${h.score != null ? `<span class="badge muted" title="${esc(h.score_kind)}">score ${Number(h.score).toFixed(3)}</span>` : ""}</div>
      <div class="mt-2 text-neutral-100">${esc(h.title || "")}</div>
      ${h.symptoms ? `<div class="wf-k">Symptoms</div><div>${esc(h.symptoms)}</div>` : ""}
      ${h.root_cause ? `<div class="wf-k">Root cause</div><div>${esc(h.root_cause)}</div>` : ""}
      ${h.remediation ? `<div class="wf-k">Remediation</div><div>${esc(h.remediation)}</div>` : ""}
      ${h.failed_attempts ? `<div class="wf-k">Did not work</div><div>${esc(h.failed_attempts)}</div>` : ""}
      <div class="wf-k">Why relevant</div><div>Returned by memory recall for this incident (${h.evidence.length} matching fact${h.evidence.length === 1 ? "" : "s"}); source: ${esc(h.record_source)}.</div>
      <details><summary>Inspect retrieved memory facts</summary>${h.evidence.map((e) => `<div class="wf-log">${esc(e.type)} · ${esc((e.occurred || "").slice(0, 10))}\n${esc(e.text)}</div>`).join("")}</details></div>`;
  }

  function actionCard(a) {
    const st = W.actions[a.action_id] || a;
    const ev = a.evidence_type === "historical evidence";
    let btns = "";
    if (st.status === "awaiting_approval") btns = `<button class="btn-primary sm" data-op="approve" data-id="${a.action_id}">Approve</button><button class="btn-ghost sm" data-op="reject" data-id="${a.action_id}">Reject</button>`;
    else if (st.status === "approved") btns = `<button class="btn-primary sm" data-op="simulate" data-id="${a.action_id}">Run simulated execution</button>`;
    else if (st.status === "simulated") btns = `<label class="flex items-center gap-2"><input type="checkbox" class="accent-neutral-300" data-chk="${a.action_id}" data-c="${esc(a.verification || "Symptoms back to baseline")}"> ${esc(a.verification || "Symptoms back to baseline")}</label><label class="flex items-center gap-2"><input type="checkbox" class="accent-neutral-300" data-chk="${a.action_id}" data-c="No new related alerts for 10 minutes"> No new related alerts for 10 minutes</label><button class="btn-primary sm" data-op="verify" data-id="${a.action_id}">Record verification</button>`;
    return `<div class="wf-card ${st.status === "verified_passed" ? "hl" : ""}">
      <div class="flex flex-wrap items-center gap-1"><span class="badge ${a.risk === "high" ? "high" : ""}">risk: ${esc(a.risk)}</span><span class="badge ${ev ? "solid" : "muted"}">${esc(a.evidence_type)}</span>${a.incident_id ? `<span class="badge">${esc(a.incident_id)}</span>` : ""}<span class="badge">historical outcome: ${esc(a.historical_outcome)}</span><span class="ml-auto badge solid">${esc(st.status_label)}</span></div>
      <div class="mt-2 text-neutral-100 font-semibold">${esc(a.title)}</div>
      <div class="wf-grid">
        <div><div class="wf-k">Explanation</div>${esc(a.explanation || "—")}</div>
        <div><div class="wf-k">Why recommended</div>${esc(a.why || "—")}</div>
        <div><div class="wf-k">Previous fix</div>${esc(a.previous_fix || "—")}</div>
        <div><div class="wf-k">Expected result</div>${esc(a.expected_result || "—")}</div>
        <div><div class="wf-k">Verification</div>${esc(a.verification || "—")}</div>
        <div><div class="wf-k">Rollback</div>${esc(a.rollback || "—")}</div>
      </div>
      ${a.recorded_remediation ? `<details><summary>Stored record for ${esc(a.incident_id)}</summary><div class="wf-log">Fix that worked: ${esc(a.recorded_remediation)}${a.recorded_failed_attempts ? `\nDid NOT work: ${esc(a.recorded_failed_attempts)}` : ""}</div></details>` : ""}
      ${st.log ? `<div class="wf-log">${st.log.map(esc).join("\n")}\nreal_infrastructure_changed: false</div>` : ""}
      ${st.verification ? `<div class="wf-log">${st.verification.map((c) => `${c.passed ? "✓" : "✗"} ${esc(c.check)}`).join("\n")}\n(${esc(st.verification_basis)})</div>` : ""}
      ${st.error ? errBox(st.error) : ""}
      <div class="flex flex-wrap items-center gap-3 mt-3">${btns}</div></div>`;
  }

  function render() {
    stepper();
    const c = W.cmp;
    ["wfCompareBox", "wfInvBox", "wfSimBox", "wfActBox"].forEach((id) => ($(id).hidden = !c));
    if (c) {
      $("wfSame").textContent = c.same_input ? "· identical input in both modes ✓" : "";
      $("wfTable").innerHTML = c.table.map((r) => `<tr><td>${esc(r.attribute)}</td><td>${esc(r.off)}</td><td>${esc(r.on)}</td></tr>`).join("");
      $("wfInvOff").innerHTML = invCard(c.off);
      $("wfInvOn").innerHTML = invCard(c.on);
      $("wfSimilar").innerHTML = c.on.historical_incidents.length ? c.on.historical_incidents.map((h) => simCard(h)).join("")
        : '<div class="muted">No relevant past incidents were found in memory for this incident. Recommendations below are general diagnostic suggestions.</div>';
      $("wfActions").innerHTML = c.on.investigation.actions.map(actionCard).join("") || '<div class="muted">No actions proposed.</div>';
    }
    const verified = Object.values(W.actions).some((a) => a.status === "verified_passed");
    $("wfPmBox").hidden = !verified;
    $("wfLearnBox").hidden = !(W.retained && W.retained.retained);
    if (W.sample && !$("wfAlert2").value) $("wfAlert2").value = W.sample.followup || W.alert;
    if (W.pmOut) $("pmOut").innerHTML = W.pmOut;
    if (W.learnOut) $("wfLearnOut").innerHTML = W.learnOut;
  }

  async function run() {
    const alert = $("wfAlert").value.trim();
    if (!alert) { status("Enter an incident description first."); return; }
    $("wfRun").disabled = true; status("investigating with Memory OFF and Memory ON… (5–30 s)");
    try {
      const c = await post("/api/workflow/compare", { alert });
      W = { ...W, alert, cmp: c, actions: {}, retained: null, pmOut: "", learnOut: "", learn: null };
      c.on.investigation.actions.forEach((a) => (W.actions[a.action_id] = { ...a }));
      const top = (c.on.investigation.potential_root_causes || [])[0];
      $("pmTitle").value = (W.sample && W.sample.resolution.title) || `Incident on ${c.on.investigation.service || "service"}`;
      $("pmService").value = c.on.investigation.service || (W.sample && W.sample.service) || "";
      $("pmCause").value = (top && top.basis === "evidence" ? top.cause : (W.sample && W.sample.resolution.root_cause)) || (top ? top.cause : "");
      $("pmLessons").value = ""; $("pmPrevent").value = ""; $("pmOut").innerHTML = ""; $("wfLearnOut").innerHTML = "";
      save(); render(); status(`done · OFF ${c.off.seconds}s · ON ${c.on.seconds}s`);
    } catch (e) { status(""); $("wfStatus").innerHTML = errBox(e.message); }
    finally { $("wfRun").disabled = false; }
  }

  $("wfActions").addEventListener("click", async (ev) => {
    const b = ev.target.closest("button[data-op]"); if (!b) return;
    const id = b.dataset.id, op = b.dataset.op, a = W.actions[id];
    if (op === "approve" && a.risk === "high" && !confirm(`HIGH RISK action:\n${a.title}\n\nApprove for SIMULATED execution?`)) return;
    const body = { token: a.token, op };
    if (op === "verify") body.checks = [...document.querySelectorAll(`input[data-chk="${id}"]`)].map((x) => ({ check: x.dataset.c, passed: x.checked }));
    b.disabled = true;
    try { const r = await post("/api/workflow/action", body); W.actions[id] = { ...a, ...r, error: null }; }
    catch (e) { W.actions[id] = { ...a, error: e.message }; }
    save(); render();
  });

  function pmBody(retain) {
    return { alert: W.alert, title: $("pmTitle").value, service: $("pmService").value, severity: (W.sample && W.sample.severity) || "SEV2",
      root_cause: $("pmCause").value, lessons: $("pmLessons").value, prevention: $("pmPrevent").value, test: $("pmTest").checked,
      action_tokens: Object.values(W.actions).filter((a) => ["verified_passed", "verified_failed", "rejected"].includes(a.status)).map((a) => a.token), retain };
  }
  const pmView = (d) => `<div class="wf-card"><div class="wf-k">Postmortem ${esc(d.incident_id)}</div>
    <div class="wf-grid"><div><div class="wf-k">Incident summary</div>${esc(d.postmortem.incident_summary)}</div><div><div class="wf-k">Root cause</div>${esc(d.postmortem.root_cause)}</div>
    <div><div class="wf-k">Actions taken</div>${esc(d.postmortem.actions_taken)}</div><div><div class="wf-k">Result</div>${esc(d.postmortem.result)}</div>
    <div><div class="wf-k">Rejected / failed</div>${esc(d.postmortem.unsuccessful_or_rejected)}</div><div><div class="wf-k">Lessons learned</div>${esc(d.postmortem.lessons_learned || "—")}</div>
    <div><div class="wf-k">Prevention</div>${esc(d.postmortem.prevention || "—")}</div></div></div>`;
  $("pmPreview").onclick = async () => {
    try { const d = await post("/api/workflow/postmortem", pmBody(false)); W.pmOut = pmView(d); }
    catch (e) { W.pmOut = errBox(e.message); }
    save(); render();
  };
  $("pmRetain").onclick = async () => {
    $("pmRetain").disabled = true; $("pmOut").innerHTML = '<div class="muted">writing to memory… (can take 10–30 s)</div>';
    try {
      const d = await post("/api/workflow/postmortem", pmBody(true));
      const r = d.retention;
      if (r.retained || (r.duplicate && W.retained && W.retained.retained)) W.retained = { ...r, retained: true };
      const msg = r.retained ? `<div class="text-neutral-100">✓ ${esc(r.message)} (backend: ${esc(d.memory_backend)})</div>`
        : r.duplicate ? `<div class="warn">${esc(r.message)}</div>` : errBox(`${r.message} ${r.error || ""}`);
      W.pmOut = msg + pmView(d);
    } catch (e) { W.pmOut = errBox(e.message); }
    finally { $("pmRetain").disabled = false; save(); render(); }
  };

  $("wfLearnRun").onclick = async () => {
    const alert = $("wfAlert2").value.trim(); if (!alert || !W.retained) return;
    $("wfLearnRun").disabled = true; $("wfLearnOut").innerHTML = '<div class="muted">recalling and investigating…</div>';
    try {
      const [inv, check] = await Promise.all([post("/api/workflow/investigate", { alert, use_memory: true }),
        post("/api/workflow/recall-check", { alert, incident_id: W.retained.incident_id })]);
      W.learn = { inv, check };
      const id = W.retained.incident_id;
      const before = W.cmp.on.historical_incidents.map((h) => h.id);
      const hit = inv.historical_incidents.find((h) => h.id === id);
      W.learnOut = `<div class="wf-card"><div class="wf-k">Before learning (first incident)</div><div>${before.map((x) => `<span class="badge">${esc(x)}</span>`).join("") || "none"}</div>
          <div class="wf-k">Retained postmortem</div><div><span class="badge solid">${esc(id)}</span> was not in memory yet.</div></div>
        <div class="wf-card hl"><div class="wf-k">After learning (related incident)</div><div>${inv.historical_incidents.map((h) => `<span class="badge ${h.id === id ? "solid" : ""}">${esc(h.id)}</span>`).join("") || "none"}</div>
          <div class="wf-k">Recall check</div><div>${check.found ? `✓ Memory returned ${esc(id)} (${check.matching_facts.length} fact(s), incident rank ${check.rank ?? "n/a"} of returned incidents).` : `✗ ${esc(id)} was not returned by recall for this text (yet). Try wording closer to the original, or retry in a few seconds.`}</div>
          ${check.matching_facts.length ? `<details><summary>Retrieved facts</summary>${check.matching_facts.map((f) => `<div class="wf-log">${esc(f)}</div>`).join("")}</details>` : ""}
          ${hit ? `<div class="wf-k">How it's used</div><div>The new investigation's top incidents include the retained postmortem, so its fix and failed attempts feed the recommendations.</div>` : ""}</div>
        <div class="md:col-span-2">${invCard(inv)}</div>`;
    } catch (e) { W.learnOut = errBox(e.message); }
    finally { $("wfLearnRun").disabled = false; save(); render(); }
  };

  $("wfRun").onclick = run;
  $("wfReset").onclick = () => { sessionStorage.removeItem(KEY); W = { alert: "", cmp: null, actions: {}, retained: null, sample: null }; $("wfAlert").value = ""; $("wfAlert2").value = ""; $("pmOut").innerHTML = ""; $("wfLearnOut").innerHTML = ""; status(""); render(); };
  $("wfPick").onchange = () => { const s = samples.find((x) => x.key === $("wfPick").value); if (s) { W.sample = s; $("wfAlert").value = s.alert; $("wfAlert2").value = s.followup; } };
  api("/api/agent/samples").then((j) => {
    samples = j.samples || [];
    $("wfPick").innerHTML = '<option value="">Pick a sample incident…</option>' + samples.map((s) => `<option value="${esc(s.key)}">${esc(s.alert.slice(0, 70))}…</option>`).join("");
  }).catch(() => {});
  api("/api/health").then((h) => ($("wfBackend").textContent = `memory: ${h.memory} · llm: ${h.llm || "none (deterministic fallback)"}`)).catch(() => ($("wfBackend").textContent = "API unreachable"));
  try { const saved = JSON.parse(sessionStorage.getItem(KEY) || "null"); if (saved && saved.cmp) { W = saved; $("wfAlert").value = W.alert; status("restored your in-progress workflow"); } } catch {}
  render();
})();
