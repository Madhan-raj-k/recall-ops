const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = (s) => DOMPurify.sanitize(marked.parse(s || ""));
const spinner = (msg) => `<div class="flex items-center gap-2 text-slate-400"><svg class="spin w-4 h-4" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity=".25"/><path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="3"/></svg>${esc(msg)}</div>`;
const errBox = (m) => `<div class="rounded-lg border border-rose-800 bg-rose-950/40 text-rose-200 p-3 text-sm">${esc(m)}</div>`;
const warnBox = (ws) => (ws || []).map((w) => `<div class="rounded-lg border border-amber-800 bg-amber-950/30 text-amber-200 p-2 text-xs mb-2">${esc(w)}</div>`).join("");
const badge = (c) => {
  if (!c) return "";
  const col = { low: "bg-rose-900/60 text-rose-200", medium: "bg-amber-900/60 text-amber-200", high: "bg-emerald-900/60 text-emerald-200" }[c];
  return `<span class="text-xs px-2 py-0.5 rounded-full ${col}">confidence: ${c}</span>`;
};
const typeChip = (t) => `<span class="text-[10px] uppercase px-1.5 py-0.5 rounded ${t === "observation" ? "bg-sky-900/60 text-sky-200" : "bg-slate-700 text-slate-200"}">${esc(t)}</span>`;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "content-type": "application/json" }, ...opts });
  let body = null;
  try { body = await r.json(); } catch { /* non-json */ }
  if (!r.ok) {
    let d = body && body.detail;
    if (Array.isArray(d)) d = d.map((x) => x.msg).join("; ");
    throw new Error(d || `Request failed (${r.status}). Please try again.`);
  }
  return body;
}

// ---------- status + examples ----------
api("/api/health").then((h) => {
  $("status").innerHTML = `memory: <b class="text-emerald-300">${esc(h.memory)}</b> · bank <code>${esc(h.bank)}</code> · LLM: <b class="text-emerald-300">${esc(h.llm || "offline")}</b>`;
  const labels = ["💳 payments-api 5xx after deploy", "🛒 empty carts, Redis evictions", "🗄️ orders DB pool timeouts", "🔒 mobile SSL failures"];
  h.examples.forEach((ex, i) => {
    const b = document.createElement("button");
    b.className = "text-xs px-3 py-1.5 rounded-full border border-slate-700 hover:border-violet-500 hover:text-white";
    b.textContent = labels[i] || ex.slice(0, 40);
    b.title = ex;
    b.onclick = () => { $("alert").value = ex; countAlert(); runTriage(); };
    $("examples").appendChild(b);
  });
  if (!$("alert").value) { $("alert").value = h.examples[0]; countAlert(); }
}).catch(() => { $("status").innerHTML = `<span class="text-rose-300">API unavailable</span>`; });

const countAlert = () => { $("alertCount").textContent = `${$("alert").value.length}/500`; };
$("alert").addEventListener("input", countAlert);

// ---------- triage ----------
function renderPlan(target, badgeEl, res) {
  badgeEl.innerHTML = badge(res.confidence) + `<span class="text-xs text-slate-500 ml-2">${res.seconds ?? ""}s</span>`;
  target.innerHTML = warnBox(res.warnings) + md(res.plan);
}
function renderRecall(res) {
  $("recallCount").textContent = `(${res.memories.length} memories)`;
  $("sources").innerHTML = res.sources.map((s) => `<span class="text-xs px-2 py-0.5 rounded-full bg-violet-900/50 text-violet-200 font-mono">${esc(s)}</span>`).join("");
  $("recalled").innerHTML = res.memories.slice(0, 40).map((m) =>
    `<li class="border-b border-slate-800 pb-2"><div class="flex gap-2 items-center text-xs text-slate-500">${typeChip(m.type)}<span>${esc((m.occurred || "").slice(0, 10))}</span><span class="font-mono text-violet-300">${esc(m.source || "consolidated")}</span></div><div class="mt-1 text-slate-300">${esc(m.text)}</div></li>`).join("") || `<li class="text-slate-500">No memories recalled.</li>`;
  $("reflect").innerHTML = res.reflection ? md(res.reflection) : `<span class="text-slate-500">No reflect output${$("useReflect").checked ? "" : " (disabled)"}.</span>`;
}
async function runTriage() {
  const alert = $("alert").value.trim();
  if (!alert) { $("onOut").innerHTML = errBox("Type or pick an alert first."); return; }
  $("runTriage").disabled = true;
  $("offOut").innerHTML = spinner("Planning without memory…");
  $("onOut").innerHTML = spinner("Recalling past incidents from Hindsight, reflecting, planning… (10–20 s)");
  $("offBadge").innerHTML = $("onBadge").innerHTML = "";
  $("recalled").innerHTML = ""; $("sources").innerHTML = ""; $("recallCount").textContent = "";
  $("reflect").innerHTML = spinner("Waiting for reflect…");
  const reflect = $("useReflect").checked;
  const off = api("/api/triage", { method: "POST", body: JSON.stringify({ alert, use_memory: false, reflect: false }) })
    .then((r) => renderPlan($("offOut"), $("offBadge"), r)).catch((e) => { $("offOut").innerHTML = errBox(e.message); });
  const on = api("/api/triage", { method: "POST", body: JSON.stringify({ alert, use_memory: true, reflect }) })
    .then((r) => { renderPlan($("onOut"), $("onBadge"), r); renderRecall(r); })
    .catch((e) => { $("onOut").innerHTML = errBox(e.message); $("reflect").innerHTML = ""; });
  await Promise.allSettled([off, on]);
  $("runTriage").disabled = false;
}
$("runTriage").onclick = runTriage;

// ---------- learning curve ----------
const LC = {
  alert: "inventory-sync: oversell alerts firing, stock counts drifting from the warehouse system after the 02:00 feed import",
  pm: {
    title: "Oversells after warehouse feed import (inventory-sync)",
    service: "inventory-sync", severity: "SEV2",
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
async function lcTriage(btn, out, badgeEl) {
  btn.disabled = true; out.innerHTML = spinner("Recalling from your sandbox bank and planning…");
  try {
    if (!sandbox) sandbox = (await api("/api/learning/start", { method: "POST" })).sandbox;
    const r = await api("/api/learning/triage", { method: "POST", body: JSON.stringify({ sandbox, alert: LC.alert }) });
    out.innerHTML = warnBox(r.warnings) + md(r.plan);
    badgeEl.innerHTML = badge(r.confidence) + `<div class="text-xs text-slate-500 mt-1">${r.memories.length} memories recalled · ${r.seconds}s</div>`;
    return r;
  } catch (e) { out.innerHTML = errBox(e.message); btn.disabled = false; return null; }
}
$("lc1").onclick = async () => { const r = await lcTriage($("lc1"), $("lcBefore"), $("lc1b")); if (r) $("lc2").disabled = false; };
$("lc2").onclick = async () => {
  $("lc2").disabled = true; $("lc2b").innerHTML = spinner("Retaining into Hindsight…");
  try {
    const r = await api("/api/learning/resolve", { method: "POST", body: JSON.stringify({ ...LC.pm, sandbox }) });
    $("lc2b").innerHTML = `<span class="text-emerald-300">Retained <b class="font-mono">${esc(r.incident_id)}</b></span>`;
    $("lc3").disabled = false;
  } catch (e) { $("lc2b").innerHTML = errBox(e.message); $("lc2").disabled = false; }
};
$("lc3").onclick = async () => { await lcTriage($("lc3"), $("lcAfter"), $("lc3b")); $("lc3").disabled = false; };

// ---------- resolve ----------
$("resForm").onsubmit = async (ev) => {
  ev.preventDefault();
  const fd = Object.fromEntries(new FormData($("resForm")).entries());
  fd.time_to_resolve_min = fd.time_to_resolve_min ? parseInt(fd.time_to_resolve_min, 10) : null;
  $("resBtn").disabled = true; $("resOut").innerHTML = spinner("Retaining postmortem into Hindsight…");
  try {
    const r = await api("/api/resolve", { method: "POST", body: JSON.stringify(fd) });
    $("resOut").innerHTML = `<div class="rounded-lg border border-emerald-800 bg-emerald-950/30 text-emerald-200 p-3">Retained <b class="font-mono">${esc(r.incident_id)}</b>. Triage a similar alert above to see it recalled.</div>
      <details class="mt-2"><summary class="cursor-pointer text-slate-400">What was sent to Hindsight retain()</summary><pre class="mt-2 whitespace-pre-wrap text-xs bg-[#0b1020] border border-slate-700 rounded-xl p-3">${esc(r.memory_text)}\n\ntags: ${esc(r.tags.join(", "))}\ntimestamp: ${esc(r.timestamp)}</pre></details>`;
    $("resForm").reset();
  } catch (e) { $("resOut").innerHTML = errBox(e.message); }
  $("resBtn").disabled = false;
};

// ---------- explorer ----------
async function explore() {
  const q = $("exQ").value.trim();
  $("timeline").innerHTML = `<li class="ml-4">${spinner(q ? "Recalling…" : "Loading latest memories…")}</li>`;
  try {
    const r = await api(`/api/explore?q=${encodeURIComponent(q)}&limit=60`);
    const rows = [...r.memories];
    if (!q) rows.sort((a, b) => (b.occurred || "").localeCompare(a.occurred || ""));
    $("timeline").innerHTML = rows.map((m) => `<li class="ml-4"><span class="absolute -left-1.5 mt-1.5 w-3 h-3 rounded-full ${m.type === "observation" ? "bg-sky-400" : "bg-violet-400"}"></span>
      <div class="flex gap-2 items-center text-xs text-slate-500">${typeChip(m.type)}<span>${esc((m.occurred || "").slice(0, 10))}</span><span class="font-mono text-violet-300">${esc(m.source || "consolidated")}</span></div>
      <div class="text-sm text-slate-300 mt-0.5">${esc(m.text)}</div></li>`).join("") || `<li class="ml-4 text-slate-500">No memories found.</li>`;
  } catch (e) { $("timeline").innerHTML = `<li class="ml-4">${errBox(e.message)}</li>`; }
}
$("exBtn").onclick = explore;
$("exQ").addEventListener("keydown", (e) => { if (e.key === "Enter") explore(); });
explore();
