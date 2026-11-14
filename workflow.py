"""Evidence-driven incident workflow:

INVESTIGATE -> RECOMMEND -> APPROVE -> SIMULATE -> VERIFY -> POSTMORTEM -> RETAIN -> RECALL

Honesty rules enforced in code (not just in prompts):
* Memory OFF never calls the memory backend.
* An action may cite only incident IDs that recall actually returned; anything else is
  downgraded to a "general diagnostic suggestion" with outcome "unknown".
* Execution is always simulated; responses carry ``real_infrastructure_changed: False``.
* Approval != resolved. Resolution requires a separate verification step.
* Retention is reported as successful only when the memory backend call succeeded.

Action state travels in an HMAC-signed token so a serverless backend can enforce the
state machine without a database, and the browser cannot forge "approved"/"verified".
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass

from incidents_data import seed_incidents
from llm import LLM, LLMError
from memory import IncidentMemory, MemoryError_
from models import Incident, utcnow

log = logging.getLogger(__name__)

RISKS = ("low", "medium", "high")
OUTCOMES = ("successful", "failed", "unknown")
GENERAL = "general diagnostic suggestion"

# ---------------------------------------------------------------- helpers
_SVC = re.compile(r"\b([a-z0-9]+(?:-[a-z0-9]+)*-(?:api|service|web|db|gateway|events|cdn|cluster))\b")
_TTR = re.compile(r"time to resolve:\s*(\d+)", re.I)


def guess_service(text: str) -> str | None:
    m = _SVC.search((text or "").lower())
    return m.group(1) if m else None


def catalog() -> dict[str, dict]:
    """Recorded incident records (synthetic ShopNova postmortems) keyed by incident ID."""
    return {
        i.incident_id: dict(
            id=i.incident_id, title=i.title, service=i.service, severity=i.severity, symptoms=i.symptoms,
            root_cause=i.root_cause, remediation=i.what_worked, failed_attempts=i.what_failed,
            outcome="successful" if i.what_worked else "unknown", ttr_min=i.time_to_resolve_min,
            occurred=i.occurred_at.isoformat(),
        )
        for i in seed_incidents()
    }


def _parse_memory_text(text: str) -> dict:
    """Pull fields out of a full postmortem memory text (offline store / retained postmortems)."""
    out = {}
    for key, pat in (("title", r"^Incident [^:]+:\s*(.+?)\.?$"), ("symptoms", r"^Symptoms observed:\s*(.+)$"),
                     ("root_cause", r"^Root cause:\s*(.+)$"), ("remediation", r"^Fix that worked:\s*(.+)$"),
                     ("failed_attempts", r"^Attempts that did NOT work.*?:\s*(.+)$")):
        m = re.search(pat, text or "", re.M)
        if m:
            out[key] = m.group(1).strip()
    return out


def similar_incidents(memories, limit: int = 3) -> list[dict]:
    """Collapse fact-level recall hits into up to `limit` distinct incidents, using only what recall returned."""
    cat = catalog()
    groups: dict[str, dict] = {}
    for m in memories:
        if not m.document_id:
            continue  # consolidated observation without a source incident
        g = groups.get(m.document_id)
        if g is None:
            rec = cat.get(m.document_id)
            g = dict(rec) if rec else {"id": m.document_id, "service": None, "outcome": "unknown", **_parse_memory_text(m.text)}
            if not rec:
                svc = next((t.split(":", 1)[1] for t in (m.tags or []) if t.startswith("service:")), None)
                g["service"] = svc
                if g.get("remediation"):
                    g["outcome"] = "successful"
                g["record_source"] = "retained memory"
            else:
                g["record_source"] = "incident record (synthetic ShopNova data)"
            g.update(evidence=[], score=None, score_kind=None)
            groups[m.document_id] = g
        if m.score is not None and (g["score"] is None or m.score > g["score"]):
            g["score"] = round(float(m.score), 3)
            g["score_kind"] = "retrieval score from memory backend"
        if len(g["evidence"]) < 4:
            g["evidence"].append({"text": m.text[:400], "type": m.type, "occurred": m.occurred})
    return list(groups.values())[:limit]


# ---------------------------------------------------------------- investigation
INVESTIGATE_PROMPT = """You are RecallOps, an incident-response investigator for an e-commerce on-call team.
Return ONLY a JSON object (no prose, no code fences) with keys:
"summary": string, "service": string|null, "symptoms": [string],
"potential_root_causes": [{"cause": string, "basis": "evidence"|"assumption", "incident_ids": [string]}],
"supporting_evidence": [{"claim": string, "incident_id": string}],
"missing_information": [string], "next_steps": [string],
"previous_failures": [{"attempt": string, "incident_id": string}],
"actions": [{"title": string, "explanation": string, "why": string, "incident_id": string|null,
  "previous_fix": string|null, "historical_outcome": "successful"|"failed"|"unknown",
  "risk": "low"|"medium"|"high", "expected_result": string, "verification": string, "rollback": string|null}]
Rules: low risk = read-only diagnostics; medium = reversible changes; high = disruptive (rollback, restart,
delete, failover). Cite ONLY incident IDs listed under HISTORICAL INCIDENTS. If none are listed, every action
must have incident_id null and historical_outcome "unknown", supporting_evidence must be [], and you must say in
missing_information that no organizational history was available. Never invent incidents or outcomes.
Give 2-3 actions, the first one a low-risk diagnostic. Keep every string under 30 words."""

_HIGH = re.compile(r"\b(roll(?:ed|ing)? ?back|rollback|restart|delet|drop|flush|failover|terminat|kill|unlink|reindex|re-?appl|purg|redeploy)\w*", re.I)
_MED = re.compile(r"\b(scal|increas|rais|set|enabl|disabl|pin|mov|chang|updat|config|switch|resiz)\w*", re.I)


def risk_of(text: str) -> str:
    if _HIGH.search(text or ""):
        return "high"
    if _MED.search(text or ""):
        return "medium"
    return "low"


def _json_from(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in LLM output")
    return json.loads(text[start:end + 1])


def _as_list(v) -> list:
    return v if isinstance(v, list) else ([] if v in (None, "") else [v])


def _validate(inv: dict, valid_ids: set[str], records: dict[str, dict]) -> dict:
    """Strip anything the model cited that recall did not return; normalise enums."""
    removed = []

    def ok(i):
        return isinstance(i, str) and i in valid_ids

    for c in _as_list(inv.get("potential_root_causes")):
        if isinstance(c, dict):
            ids = [i for i in _as_list(c.get("incident_ids")) if ok(i)]
            removed += [i for i in _as_list(c.get("incident_ids")) if not ok(i)]
            c["incident_ids"] = ids
            c["basis"] = "evidence" if ids and c.get("basis") == "evidence" else "assumption"
    ev = []
    for e in _as_list(inv.get("supporting_evidence")):
        if isinstance(e, dict) and ok(e.get("incident_id")):
            ev.append(e)
        elif isinstance(e, dict):
            removed.append(e.get("incident_id"))
    inv["supporting_evidence"] = ev
    inv["previous_failures"] = [f for f in _as_list(inv.get("previous_failures")) if isinstance(f, dict) and ok(f.get("incident_id"))]
    actions = []
    for a in _as_list(inv.get("actions"))[:5]:
        if not isinstance(a, dict) or not a.get("title"):
            continue
        if a.get("incident_id") and not ok(a.get("incident_id")):
            removed.append(a.get("incident_id"))
            a["incident_id"] = None
        if a.get("incident_id"):
            a["evidence_type"] = "historical evidence"
            rec = records.get(a["incident_id"], {})
            a["recorded_remediation"] = rec.get("remediation")
            a["recorded_failed_attempts"] = rec.get("failed_attempts")
            if a.get("historical_outcome") not in OUTCOMES:
                a["historical_outcome"] = "unknown"
        else:
            a["evidence_type"] = GENERAL
            a["historical_outcome"] = "unknown"
            a["previous_fix"] = None
        model_risk = a.get("risk") if a.get("risk") in RISKS else "medium"
        # Never let the model downgrade an obviously disruptive action.
        heur = risk_of(" ".join(str(a.get(k) or "") for k in ("title", "explanation")))
        a["risk"] = max(model_risk, heur, key=RISKS.index)
        actions.append(a)
    inv["actions"] = actions
    for k in ("symptoms", "missing_information", "next_steps"):
        inv[k] = [str(x) for x in _as_list(inv.get(k))]
    if removed:
        inv.setdefault("validation_notes", []).append(
            f"Removed {len([r for r in removed if r])} reference(s) not returned by memory: {sorted({r for r in removed if r})}")
    return inv


def _offline_investigation(alert: str, incidents: list[dict], use_memory: bool) -> dict:
    """Deterministic investigation from the alert text and retrieved records only (no LLM)."""
    service = guess_service(alert)
    inv = {
        "summary": f"Alert received: {alert}",
        "service": service,
        "symptoms": [alert],
        "potential_root_causes": [],
        "supporting_evidence": [],
        "missing_information": [],
        "next_steps": [
            "Check deploys, config and feature-flag changes in the last 2 hours.",
            "Check saturation of the affected service and its dependencies (CPU, memory, connection pools, queues).",
        ],
        "previous_failures": [],
        "actions": [{
            "title": "Collect diagnostics (read-only)",
            "explanation": "Pull recent deploy history, error rate, latency and dependency health for the service.",
            "why": "Establishes facts before any change is made.", "incident_id": None, "previous_fix": None,
            "historical_outcome": "unknown", "risk": "low",
            "expected_result": "A confirmed timeline and the failing dependency, if any.",
            "verification": "Diagnostics collected and attached to the incident.", "rollback": None,
        }],
    }
    if not use_memory:
        inv["missing_information"].append("Memory OFF: no organizational incident history was consulted.")
    elif not incidents:
        inv["missing_information"].append("No similar past incidents were found in memory, so there is no historical evidence.")
    for r in incidents:
        if r.get("root_cause"):
            inv["potential_root_causes"].append({"cause": r["root_cause"], "basis": "evidence", "incident_ids": [r["id"]]})
            inv["supporting_evidence"].append({"claim": f"{r['id']} ({r.get('title', '')}) had similar symptoms: {r.get('symptoms', '')}", "incident_id": r["id"]})
        if r.get("failed_attempts"):
            inv["previous_failures"].append({"attempt": r["failed_attempts"], "incident_id": r["id"]})
        if r.get("remediation"):
            inv["actions"].append({
                "title": r["remediation"].split(";")[0].split(". ")[0][:140],
                "explanation": r["remediation"], "why": f"This is what resolved {r['id']} ({r.get('title', '')}).",
                "incident_id": r["id"], "previous_fix": r["remediation"], "historical_outcome": r.get("outcome", "unknown"),
                "risk": risk_of(r["remediation"]), "expected_result": "Symptoms return to baseline as in the past incident.",
                "verification": "Error rate and latency back to baseline for 10 minutes; alert clears.",
                "rollback": "Revert the change if metrics do not improve within 10 minutes.",
            })
    if incidents:
        inv["missing_information"].append("Confirm whether a deploy or config change lines up with the alert start.")
    else:
        inv["potential_root_causes"].append({"cause": "Unknown: insufficient evidence.", "basis": "assumption", "incident_ids": []})
    inv["actions"] = inv["actions"][:4]
    return inv


def investigate(alert: str, memory: IncidentMemory | None, llm: LLM | None, use_memory: bool) -> dict:
    alert = (alert or "").strip()
    if not alert:
        raise ValueError("Incident description is empty.")
    t0 = time.time()
    warnings: list[str] = []
    memories, incidents, recall_called = [], [], False
    if use_memory and memory is not None:
        recall_called = True
        try:
            memories = memory.recall_similar(alert)
        except MemoryError_ as exc:
            warnings.append(f"Memory recall failed; investigating without history: {exc}")
        incidents = similar_incidents(memories)
    records = {r["id"]: r for r in incidents}
    llm_used = False
    inv = None
    if llm is not None:
        hist = "\n".join(
            f"- {r['id']} | service={r.get('service')} | {r.get('title', '')} | symptoms: {r.get('symptoms', '?')} | "
            f"root cause: {r.get('root_cause', '?')} | fix that worked: {r.get('remediation', '?')} | "
            f"did NOT work: {r.get('failed_attempts') or 'n/a'} | facts: {' / '.join(e['text'] for e in r['evidence'][:2])}"
            for r in incidents
        )
        user = f"NEW INCIDENT:\n{alert}\n\nHISTORICAL INCIDENTS:\n{hist or '(none: memory ' + ('returned no matches' if use_memory else 'is OFF') + ')'}"
        if hasattr(llm, "max_tokens"):
            llm.max_tokens = max(llm.max_tokens, 3500)  # structured JSON + reasoning tokens
        if hasattr(llm, "max_retries"):
            llm.max_retries = min(llm.max_retries, 2)  # stay inside the serverless time limit
        try:
            inv = _validate(_json_from(llm.complete(INVESTIGATE_PROMPT, user)), set(records), records)
            llm_used = True
        except (LLMError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"LLM unavailable or returned invalid output; using deterministic investigation: {str(exc)[:160]}")
    if inv is None:
        inv = _validate(_offline_investigation(alert, incidents, use_memory), set(records), records)
    if not inv.get("service"):
        inv["service"] = guess_service(alert)
    for a in inv["actions"]:
        a.update(issue_action(a))
    return {
        "alert": alert, "use_memory": use_memory, "memory_recall_called": recall_called,
        "memories_returned": len(memories), "historical_incidents": incidents,
        "investigation": inv, "llm_used": llm_used, "warnings": warnings,
        "seconds": round(time.time() - t0, 2),
    }


def compare_rows(off: dict, on: dict) -> list[dict]:
    def inv_s(r):
        i = r["investigation"]
        causes = "; ".join(c["cause"] for c in i.get("potential_root_causes", [])[:2])
        return causes or i.get("summary", "")

    def hist(r):
        return ", ".join(f"{h['id']} ({h.get('title', '')})" for h in r["historical_incidents"]) or "None retrieved"

    def fix(r):
        acts = [a for a in r["investigation"]["actions"] if a.get("risk") != "low"] or r["investigation"]["actions"]
        return acts[0]["title"] if acts else "None"

    def fails(r):
        if not r["use_memory"]:
            return "None: no organizational history consulted"
        f = r["investigation"].get("previous_failures", [])
        return "; ".join(f"{x['attempt']} ({x['incident_id']})" for x in f) or "No recorded failed attempts retrieved"

    return [
        {"attribute": "Investigation", "off": inv_s(off), "on": inv_s(on)},
        {"attribute": "Historical evidence", "off": hist(off), "on": hist(on)},
        {"attribute": "Recommended fix", "off": fix(off), "on": fix(on)},
        {"attribute": "Previous failure awareness", "off": fails(off), "on": fails(on)},
    ]


# ---------------------------------------------------------------- approval state machine
_KEY = None


def _key() -> bytes:
    global _KEY
    if _KEY is None:
        seed = os.getenv("RECALLOPS_SIGNING_KEY") or os.getenv("HINDSIGHT_API_KEY") or os.getenv("GROQ_API_KEY")
        _KEY = hashlib.sha256(("recallops-actions:" + seed).encode()).digest() if seed else secrets.token_bytes(32)
    return _KEY


def sign(state: dict) -> str:
    raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    mac = hmac.new(_key(), raw, hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + mac


def unsign(token: str) -> dict:
    try:
        b64, mac = token.rsplit(".", 1)
        raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Malformed action token.") from exc
    if not hmac.compare_digest(mac, hmac.new(_key(), raw, hashlib.sha256).hexdigest()[:32]):
        raise ValueError("Action token signature is invalid.")
    return json.loads(raw)


STATUS_LABEL = {
    "awaiting_approval": "Awaiting approval",
    "approved": "Approved",
    "rejected": "Rejected",
    "simulated": "Simulated execution completed",
    "verified_passed": "Verification passed",
    "verified_failed": "Verification failed",
}
TRANSITIONS = {
    "approve": ({"awaiting_approval"}, "approved"),
    "reject": ({"awaiting_approval"}, "rejected"),
    "simulate": ({"approved"}, "simulated"),
}


def issue_action(a: dict) -> dict:
    state = {"aid": secrets.token_hex(4), "title": str(a.get("title"))[:200], "risk": a.get("risk"),
             "incident_id": a.get("incident_id"), "status": "awaiting_approval", "history": []}
    return {"action_id": state["aid"], "status": state["status"], "status_label": STATUS_LABEL[state["status"]],
            "requires_approval": True, "token": sign(state)}


def transition(token: str, op: str, checks: list[dict] | None = None) -> dict:
    state = unsign(token)
    now = utcnow().isoformat()
    result: dict = {}
    if op in TRANSITIONS:
        allowed, new = TRANSITIONS[op]
        if state["status"] not in allowed:
            raise ValueError(f"Cannot {op} an action that is '{STATUS_LABEL[state['status']]}'.")
        if op == "simulate":
            result = {
                "simulated": True, "real_infrastructure_changed": False,
                "log": [
                    f"[SIMULATION] dry-run plan prepared for: {state['title']}",
                    "[SIMULATION] no commands were sent to any real system",
                    "[SIMULATION] completed; verify the outcome before resolving",
                ],
            }
        state["status"] = new
    elif op == "verify":
        if state["status"] != "simulated":
            raise ValueError("Verification requires a completed (simulated) execution first; approval alone does not resolve an incident.")
        checks = [c for c in (checks or []) if isinstance(c, dict) and str(c.get("check", "")).strip()]
        if not checks:
            raise ValueError("Provide at least one verification check result.")
        passed = all(bool(c.get("passed")) for c in checks)
        state["status"] = "verified_passed" if passed else "verified_failed"
        state["checks"] = [{"check": str(c["check"])[:200], "passed": bool(c.get("passed"))} for c in checks][:8]
        result = {"verification_checks": state["checks"], "verification_basis": "operator-confirmed checks in a simulated environment"}
    else:
        raise ValueError(f"Unknown operation '{op}'.")
    state["history"] = (state.get("history") or [])[-8:] + [{"op": op, "at": now}]
    return {"action_id": state["aid"], "status": state["status"], "status_label": STATUS_LABEL[state["status"]],
            "resolved": state["status"] == "verified_passed", "token": sign(state), **result}


# ---------------------------------------------------------------- postmortem + retention
_RETAINED: dict[str, str] = {}  # content hash -> incident id (per warm instance)


@dataclass
class PostmortemIn:
    alert: str
    title: str
    service: str
    severity: str
    root_cause: str
    lessons: str
    prevention: str
    test: bool = False


def build_postmortem(pm: PostmortemIn, action_tokens: list[str]) -> tuple[Incident, dict]:
    states = [unsign(t) for t in action_tokens]
    verified = [s for s in states if s["status"] == "verified_passed"]
    if not verified:
        raise ValueError("A postmortem needs at least one action whose verification passed.")
    failed = [s for s in states if s["status"] in ("verified_failed", "rejected")]
    for k in ("title", "service", "root_cause"):
        if not str(getattr(pm, k) or "").strip():
            raise ValueError(f"Missing required field: {k}")
    actions_taken = "; ".join(f"{s['title']} (SIMULATED execution, verification passed)" for s in verified)
    what_failed = "; ".join(
        f"{s['title']} ({'rejected by on-call' if s['status'] == 'rejected' else 'verification failed'})" for s in failed)
    body = json.dumps([pm.alert.strip().lower(), pm.service.strip().lower(), pm.root_cause.strip().lower(),
                       sorted(s["title"].lower() for s in verified)])
    digest = hashlib.sha256(body.encode()).hexdigest()[:8].upper()
    svc = re.sub(r"[^a-z0-9-]+", "-", pm.service.strip().lower()).strip("-")
    inc = Incident(
        incident_id=f"INC-PM-{digest}", title=pm.title.strip()[:120], service=svc, severity=pm.severity,
        occurred_at=utcnow(), symptoms=pm.alert.strip()[:500], root_cause=pm.root_cause.strip()[:1200],
        what_worked=actions_taken[:1200], what_failed=what_failed[:1200], teams=[], runbook="",
        lessons=pm.lessons.strip()[:800], prevention=pm.prevention.strip()[:800],
        simulated_resolution=True, test_record=pm.test,
    )
    return inc, {
        "incident_summary": pm.alert.strip(), "root_cause": inc.root_cause, "actions_taken": actions_taken,
        "result": "Simulated execution; operator-confirmed verification passed. No real infrastructure was changed.",
        "unsuccessful_or_rejected": what_failed or "None", "lessons_learned": inc.lessons,
        "prevention": inc.prevention, "memory_text": inc.to_memory_text(),
    }


def retain_postmortem(memory: IncidentMemory, inc: Incident) -> dict:
    """Write once per content hash. Success is reported only if the backend call returned without error."""
    if inc.incident_id in _RETAINED.values():
        return {"retained": False, "duplicate": True, "incident_id": inc.incident_id,
                "message": "This postmortem was already retained; no duplicate write was made."}
    try:
        memory.retain_incident(inc)
    except MemoryError_ as exc:
        return {"retained": False, "duplicate": False, "incident_id": inc.incident_id, "error": str(exc),
                "message": "Retention FAILED. Nothing was stored; you can retry."}
    _RETAINED[inc.incident_id] = inc.incident_id
    return {"retained": True, "duplicate": False, "incident_id": inc.incident_id,
            "message": f"Retained {inc.incident_id} to {getattr(memory, 'name', 'memory')}."}


def check_recall(memory: IncidentMemory, query: str, incident_id: str) -> dict:
    """Ask memory (real recall) whether the retained postmortem comes back for a related incident."""
    mems = memory.recall_similar(query)
    hits = [m for m in mems if m.document_id == incident_id]
    return {"incident_id": incident_id, "found": bool(hits), "memories_returned": len(mems),
            "matching_facts": [m.text[:300] for m in hits[:5]],
            "rank": next((i + 1 for i, g in enumerate(similar_incidents(mems, limit=20)) if g["id"] == incident_id), None)}
