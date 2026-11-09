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

// ---------- depth counter ----------
const ZONES = [
  [0, "surface · no memory"], [0.12, "the problem · generic answers"], [0.25, "live triage · recalling incidents"],
  [0.5, "learning curve · memory grows"], [0.66, "retain · postmortems sink in"], [0.8, "memory floor · every fact"], [0.93, "how it works"],
];
const MAX_DEPTH = 4200;
let shownDepth = 0;
function onScroll() {
  const max = document.documentElement.scrollHeight - window.innerHeight;
  const f = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
  document.documentElement.style.setProperty("--depth", f.toFixed(3));
  const target = Math.round(f * MAX_DEPTH);
  shownDepth += (target - shownDepth) * 0.25;
  $("depthNum").textContent = String(Math.round(shownDepth)).padStart(4, "0");
  $("depthFill").style.width = `${f * 100}%`;
  let z = ZONES[0][1]; for (const [k, v] of ZONES) if (f >= k) z = v;
  $("depthZone").textContent = z;
}
(function tick() { onScroll(); requestAnimationFrame(tick); })();

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
  $("svcStatus").innerHTML = `memory <b>${esc(h.memory)}</b> · bank ${esc(h.bank)}<br/>llm <b>${esc(h.llm || "offline")}</b>`;
  h.examples.forEach((ex, i) => {
    const b = document.createElement("button");
    b.className = "chip"; b.textContent = LABELS[i] || ex.slice(0, 40); b.title = ex;
    b.onclick = () => { $("alert").value = ex; countAlert(); runTriage(); };
    $("examples").appendChild(b);
  });
  if (!$("alert").value) { $("alert").value = h.examples[0]; countAlert(); }
}).catch(() => { $("svcStatus").innerHTML = `<span style="color:#e0e0e0">API unavailable</span>`; });
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
