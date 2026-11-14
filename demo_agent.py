"""End-to-end agent loop for the live demo: alert -> recall -> reason -> act -> learn.

Built on the same memory / llm modules as triage. Every step degrades gracefully:
no Hindsight key -> offline in-memory bank (seeded); no LLM key -> deterministic plan
built from the recalled incidents. The loop never fails the demo because a key is missing.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field

from agent import record_resolution
from incidents_data import seed_incidents
from llm import LLM, LLMError
from memory import IncidentMemory, MemoryError_

log = logging.getLogger(__name__)

# Sample alerts for the "Simulate alert" button. Each has a canned resolution (used to
# pre-fill the learn step) and a follow-up: a second, similar alert that should hit the
# freshly learned incident and resolve on the fast path.
SAMPLE_ALERTS = [
    dict(
        key="payments-latency",
        alert="Datadog SEV2: API latency spike on payments-api, p95 4.8s on /v2/charge (normally 300ms), DB pool wait time rising",
        followup="PagerDuty SEV2: payments-api p95 latency 5s on /v2/charge again, DB connection pool wait high",
        service="payments-api", severity="SEV2",
        resolution=dict(
            title="Payments latency spike from DB connection pool starvation",
            symptoms="payments-api p95 latency 4.8s on /v2/charge; DB pool wait time rising; no error spike.",
            root_cause="A nightly settlement batch shared the payments DB pool and held 40 of 50 connections for long transactions.",
            what_worked="Moved the settlement batch to its own pool (DB_POOL_BATCH=10) and raised payments-api pool to 80; p95 back to 300ms in 4 minutes.",
            what_failed="Scaling payments-api pods made it worse (more pods competing for the same DB connections).",
            teams="payments,dba", runbook="RB-PAY-04 pool starvation", time_to_resolve_min=18,
        ),
    ),
    dict(
        key="checkout-5xx",
        alert="PagerDuty SEV1: checkout-web 5xx rate 15% right after deploy v8.2.0, payments-api calls timing out",
        followup="PagerDuty SEV1: checkout-web 5xx 12% after deploy v8.3.0, timeouts calling payments-api",
        service="checkout-web", severity="SEV1",
        resolution=dict(
            title="Checkout 5xx after deploy: payments client timeout too low",
            symptoms="checkout-web 5xx 15% right after deploy v8.2.0; timeouts calling payments-api.",
            root_cause="v8.2.0 lowered the payments HTTP client timeout from 10s to 1s via a shared config default.",
            what_worked="Rolled back with `argocd app rollback checkout-web`, then pinned PAYMENTS_HTTP_TIMEOUT=10s in config.",
            what_failed="Restarting checkout pods only helped for about a minute.",
            teams="checkout,payments", runbook="RB-PAY-01 bad deploy rollback", time_to_resolve_min=14,
        ),
    ),
    dict(
        key="redis-eviction",
        alert="cart-service: users report empty carts, Redis evicted_keys rising fast, used_memory at 100%",
        followup="cart-service alert: carts disappearing, Redis evicted_keys spiking, memory at maxmemory",
        service="cart-service", severity="SEV2",
        resolution=dict(
            title="Cart Redis eviction from TTL-less analytics keys",
            symptoms="Empty carts; Redis evicted_keys spiking; used_memory at 100% of maxmemory.",
            root_cause="A new analytics job wrote 3M session:* keys with no TTL into the cart Redis.",
            what_worked="UNLINKed session:* keys with a SCAN script, added a 1h TTL to the job, moved analytics to its own Redis.",
            what_failed="FLUSHDB was rejected (would wipe live carts).",
            teams="cart,sre", runbook="RB-REDIS-02 memory pressure", time_to_resolve_min=21,
        ),
    ),
]

SYSTEM_PROMPT = (
    "You are RecallOps, an autonomous on-call incident agent. You get a new alert and the most similar "
    "past incidents recalled from long-term memory. Reply in Markdown with exactly these sections:\n"
    "**Reasoning** - 2-4 bullets: which past incidents match and why (cite their IDs), and what differs.\n"
    "**Likely cause** - 1-2 ranked hypotheses.\n"
    "**Fix** - numbered, concrete steps; prefer fixes that worked before.\n"
    "**Avoid** - fixes that failed before ('Nothing known' if none).\n"
    "**Confidence** - low/medium/high with a one-line reason.\n"
    "Never invent incident IDs; only cite IDs given to you."
)

_TTR = re.compile(r"time to resolve:\s*(\d+)", re.I)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]+")
_STOP = {"the", "and", "on", "of", "to", "in", "at", "is", "for", "a", "an", "with", "after", "right",
         "again", "rate", "users", "alert", "sev1", "sev2", "sev3", "pagerduty", "datadog", "high", "rising"}


def _tokens(s: str) -> set[str]:
    return {t for t in _TOKEN.findall((s or "").lower()) if t not in _STOP and len(t) > 2}


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta)


def _catalog() -> dict[str, dict]:
    out = {}
    for inc in seed_incidents():
        out[inc.incident_id] = dict(
            id=inc.incident_id, title=inc.title, service=inc.service, severity=inc.severity,
            root_cause=inc.root_cause, what_worked=inc.what_worked, what_failed=inc.what_failed,
            ttr=inc.time_to_resolve_min, learned=False,
        )
    return out


@dataclass
class AgentRun:
    alert: str
    steps: list[dict] = field(default_factory=list)
    matches: list[dict] = field(default_factory=list)
    plan: str = ""
    action: dict = field(default_factory=dict)
    fast_path: bool = False
    llm_used: bool = False
    memory_backend: str = "offline"
    est_ttr_min: int | None = None
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def step(self, name: str, detail: str, t0: float, status: str = "done") -> None:
        self.steps.append({"name": name, "detail": detail, "ms": int((time.time() - t0) * 1000), "status": status})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "alert", "steps", "matches", "plan", "action", "fast_path", "llm_used",
            "memory_backend", "est_ttr_min", "warnings", "seconds")}


def _group_matches(memories, learned: dict[str, dict], limit: int = 3) -> list[dict]:
    """Collapse fact-level recall hits into the top-N distinct incidents."""
    cat = _catalog()
    cat.update(learned)
    seen: dict[str, dict] = {}
    # Consolidated observations have no source incident; show them only if nothing else matched.
    sourced = [m for m in memories if m.document_id]
    for m in sourced or memories:
        src = m.document_id or "memory"
        if src not in seen:
            info = dict(cat.get(src) or {"id": src, "title": m.text.splitlines()[0][:120]})
            info.update(evidence=[], score=m.score)
            if info.get("ttr") is None:
                hit = _TTR.search(m.text)
                info["ttr"] = int(hit.group(1)) if hit else None
            seen[src] = info
        if len(seen[src]["evidence"]) < 3:
            seen[src]["evidence"].append(m.text[:300])
    return list(seen.values())[:limit]


def _learned_hit(alert: str, learned: dict[str, dict]) -> dict | None:
    best, best_s = None, 0.0
    for inc in learned.values():
        s = similarity(alert, " ".join([inc.get("title", ""), inc.get("symptoms", ""), inc.get("service", "")]))
        if s > best_s:
            best, best_s = inc, s
    return dict(best, score=round(best_s, 2)) if best and best_s >= 0.35 else None


def _offline_plan(alert: str, matches: list[dict]) -> str:
    if not matches:
        return ("**Reasoning**\n- No similar incidents in memory, so this is a generic checklist.\n\n"
                "**Fix**\n1. Check deploys/config changes in the last 2h; roll back if one lines up.\n"
                "2. Check saturation (CPU, memory, connection pools) and dependencies.\n\n"
                "**Avoid** - Nothing known.\n\n**Confidence** - low: no history.")
    top = matches[0]
    lines = ["**Reasoning**"]
    for m in matches:
        lines.append(f"- {m['id']} *{m.get('title', '')}* looks similar"
                     + (f" (fixed in {m['ttr']} min)" if m.get("ttr") else "") + ".")
    lines += ["", "**Likely cause**"]
    lines += [f"{i}. {m['root_cause']} ({m['id']})" for i, m in enumerate(matches[:2], 1) if m.get("root_cause")] or ["1. See matches."]
    lines += ["", "**Fix**"]
    lines += [f"{i}. {m['what_worked']} ({m['id']})" for i, m in enumerate(matches, 1) if m.get("what_worked")] or ["1. See matches."]
    fails = [f"- {m['what_failed']} ({m['id']})" for m in matches if m.get("what_failed")]
    lines += ["", "**Avoid**"] + (fails or ["- Nothing known"])
    lines += ["", f"**Confidence** - medium: grounded in {', '.join(m['id'] for m in matches)} (offline summary, no LLM)."]
    return "\n".join(lines)


def _action_for(match: dict | None, service: str) -> dict:
    fix = (match or {}).get("what_worked") or "Roll back the most recent deploy if it lines up with the alert."
    cmd = re.search(r"`([^`]+)`", fix)
    return {
        "type": "remediation",
        "service": service or (match or {}).get("service") or "unknown",
        "summary": fix.split(";")[0].split(". ")[0].strip().rstrip(".") + ".",
        "command": cmd.group(1) if cmd else None,
        "mode": "dry-run",
        "status": "awaiting on-call approval",
        "based_on": (match or {}).get("id"),
        "notify": f"#inc-{(service or 'oncall')} + paged owning team",
    }


def _guess_service(alert: str) -> str:
    m = re.search(r"\b([a-z]+(?:-[a-z]+)*-(?:api|service|web|db|gateway))\b", alert.lower())
    return m.group(1) if m else ""


def run_agent(alert: str, memory: IncidentMemory | None, llm: LLM | None,
              learned: list[dict] | None = None, memory_backend: str = "offline") -> AgentRun:
    alert = (alert or "").strip()
    if not alert:
        raise ValueError("Alert text is empty")
    t_start = time.time()
    run = AgentRun(alert=alert, memory_backend=memory_backend)
    learned_map = {d["id"]: dict(d, learned=True) for d in (learned or []) if d.get("id")}
    service = _guess_service(alert)

    t = time.time()
    run.step("alert received", f"service={service or 'unknown'}", t)

    # Fast path: the alert matches something this agent learned and retained earlier.
    hit = _learned_hit(alert, learned_map)
    if hit:
        t = time.time()
        run.fast_path = True
        run.matches = [hit]
        run.step("recall", f"matched learned incident {hit['id']} (similarity {hit['score']:.2f}); skipping deep reasoning", t)
        run.plan = (
            f"**Reasoning**\n- This alert matches **{hit['id']}** *{hit.get('title', '')}*, which this agent resolved and "
            f"retained to memory earlier.\n- Same service and symptoms, so the known fix is applied directly.\n\n"
            f"**Likely cause**\n1. {hit.get('root_cause', '')}\n\n**Fix**\n1. {hit.get('what_worked', '')}\n\n"
            f"**Avoid**\n- {hit.get('what_failed') or 'Nothing known'}\n\n"
            f"**Confidence** - high: exact match with learned incident {hit['id']}."
        )
        run.est_ttr_min = max(2, (hit.get("ttr") or 10) // 3)
        t = time.time()
        run.action = _action_for(hit, service)
        run.action["status"] = "auto-approved (known fix, high confidence) - dry-run"
        run.step("act", run.action["summary"], t)
        run.seconds = round(time.time() - t_start, 2)
        return run

    # 1) Recall
    t = time.time()
    memories = []
    if memory is not None:
        try:
            memories = memory.recall_similar(alert)
        except MemoryError_ as exc:
            run.warnings.append(f"Memory recall failed: {exc}")
    run.matches = _group_matches(memories, learned_map)
    run.step("recall", f"{len(memories)} memories -> top {len(run.matches)} incidents: "
             + (", ".join(m["id"] for m in run.matches) or "none"), t)

    # 2) Reason
    t = time.time()
    if llm is not None:
        ctx = "\n".join(
            f"- {m['id']}: {m.get('title', '')}. Cause: {m.get('root_cause', '?')}. Fix that worked: "
            f"{m.get('what_worked', '?')}. Did NOT work: {m.get('what_failed') or 'n/a'}. "
            f"TTR: {m.get('ttr') or '?'} min. Evidence: {' | '.join(m.get('evidence', [])[:2])}"
            for m in run.matches
        ) or "(no similar incidents found)"
        try:
            run.plan = llm.complete(SYSTEM_PROMPT, f"New alert:\n{alert}\n\nSimilar past incidents:\n{ctx}")
            run.llm_used = True
        except LLMError as exc:
            run.warnings.append(f"LLM unavailable, using offline plan: {exc}")
    if not run.plan:
        run.plan = _offline_plan(alert, run.matches)
    run.step("reason", "LLM plan grounded in matches" if run.llm_used else "offline plan from matches", t)

    ttrs = [m["ttr"] for m in run.matches if m.get("ttr")]
    run.est_ttr_min = int(sum(ttrs) / len(ttrs)) if ttrs else 45

    # 3) Act (safe: dry-run, needs approval)
    t = time.time()
    run.action = _action_for(run.matches[0] if run.matches else None, service)
    run.step("act", run.action["summary"], t)
    run.seconds = round(time.time() - t_start, 2)
    return run


def learn(memory: IncidentMemory, *, service: str, severity: str, title: str, symptoms: str,
          root_cause: str, what_worked: str, what_failed: str = "", teams: str = "", runbook: str = "",
          time_to_resolve_min: int | None = None) -> dict:
    """Retain the resolution to memory; returns the learned record the client keeps for the fast path."""
    warnings = []
    try:
        inc = record_resolution(
            memory, title=title, service=service, severity=severity, symptoms=symptoms,
            root_cause=root_cause, what_worked=what_worked, what_failed=what_failed,
            teams=teams.split(","), runbook=runbook, time_to_resolve_min=time_to_resolve_min,
        )
        iid, stored = inc.incident_id, True
    except MemoryError_ as exc:  # still keep the learned record so the demo continues
        warnings.append(f"Could not retain to long-term memory: {exc}")
        iid, stored = f"INC-LOCAL-{random.randint(1000, 9999)}", False
    return {
        "learned": {
            "id": iid, "title": title, "service": service, "severity": severity, "symptoms": symptoms,
            "root_cause": root_cause, "what_worked": what_worked, "what_failed": what_failed,
            "ttr": time_to_resolve_min,
        },
        "stored": stored,
        "warnings": warnings,
    }


def parse_webhook(payload: dict) -> str:
    """Accept {alert}, Alertmanager ({alerts:[{labels,annotations}]}) or PagerDuty-ish ({event:{data:{title}}})."""
    if not isinstance(payload, dict):
        return ""
    if payload.get("alert"):
        return str(payload["alert"])
    if payload.get("alerts"):
        a = payload["alerts"][0] or {}
        labels, ann = a.get("labels") or {}, a.get("annotations") or {}
        parts = [labels.get("severity", ""), labels.get("alertname", ""), labels.get("service", ""),
                 ann.get("summary", ""), ann.get("description", "")]
        return " ".join(p for p in parts if p).strip()
    data = ((payload.get("event") or {}).get("data") or {})
    return str(data.get("title") or payload.get("title") or payload.get("summary") or "")
