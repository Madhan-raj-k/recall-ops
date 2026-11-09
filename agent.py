"""Triage logic: alert in, ranked plan out, with or without Hindsight memory."""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field

from llm import LLM, LLMError
from memory import IncidentMemory, MemoryError_
from models import Incident, RecalledMemory, utcnow

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are RecallOps, an incident-response copilot for the on-call engineer of an e-commerce company. "
    "Write a concise, ranked triage plan in Markdown with these sections: "
    "'Likely cause' (1-3 ranked hypotheses), 'Do first' (numbered, concrete commands/checks), "
    "'Avoid' (things that wasted time before; write 'Nothing known' if none), "
    "'Escalate to' (team), and 'Confidence' (low/medium/high with one-line reason). "
    "Never invent past incidents. Only cite incident IDs that appear in the provided memory."
)

GENERIC_PLAN = """**Likely cause** — unknown; no incident history available.

**Do first**
1. Check what changed in the last 2 hours (deploys, config, feature flags, infra).
2. Look at the service dashboard: error rate, latency, saturation (CPU, memory, connections).
3. Check dependencies (database, cache, queues, third-party APIs) for errors.
4. If a deploy lines up with the start of the alert, consider rolling back.

**Avoid** — Nothing known (no memory).

**Escalate to** — owning team of the affected service.

**Confidence** — low: generic checklist, no history consulted."""


@dataclass
class TriageResult:
    alert: str
    use_memory: bool
    plan: str
    memories: list[RecalledMemory] = field(default_factory=list)
    reflection: str = ""
    warnings: list[str] = field(default_factory=list)
    llm_used: bool = False


def _format_memories(memories: list[RecalledMemory]) -> str:
    lines = []
    for m in memories:
        when = f" [{m.occurred[:10]}]" if m.occurred else ""
        src = f" (source: {m.document_id})" if m.document_id else ""
        lines.append(f"- ({m.type}){when}{src} {m.text}")
    return "\n".join(lines)


def _offline_memory_plan(memories: list[RecalledMemory], reflection: str) -> str:
    """Deterministic plan from recalled memories when no LLM is configured."""
    worked, failed, causes = [], [], []
    for m in memories:
        for line in m.text.splitlines():
            low = line.lower()
            if "did not work" in low:
                failed.append(line.split(":", 1)[-1].strip())
            elif low.startswith("fix that worked") or "resolved by" in low or "fixed by" in low:
                worked.append(line.split(":", 1)[-1].strip())
            elif low.startswith("root cause"):
                causes.append(line.split(":", 1)[-1].strip())
    ids = sorted({m.document_id for m in memories if m.document_id})
    out = ["**Likely cause** (from similar past incidents)"]
    out += [f"{i}. {c}" for i, c in enumerate(causes[:3], 1)] or ["- See recalled memories below."]
    out.append("\n**Do first** (fixes that worked before)")
    out += [f"{i}. {w}" for i, w in enumerate(worked[:4], 1)] or ["- See recalled memories below."]
    out.append("\n**Avoid** (tried before, did not help)")
    out += [f"- {f}" for f in failed[:4]] or ["- Nothing known"]
    if reflection:
        out.append(f"\n**Memory insight** — {reflection}")
    out.append(f"\n**Confidence** — medium: based on {', '.join(ids) or 'recalled memories'} (offline summary, no LLM).")
    return "\n".join(out)


def triage(
    alert: str,
    memory: IncidentMemory | None,
    llm: LLM | None,
    use_memory: bool = True,
    include_reflection: bool = True,
) -> TriageResult:
    alert = (alert or "").strip()
    if not alert:
        raise ValueError("Alert text is empty")
    result = TriageResult(alert=alert, use_memory=use_memory, plan="")

    if use_memory and memory is not None:
        try:
            result.memories = memory.recall_similar(alert)
        except MemoryError_ as exc:
            result.warnings.append(f"Memory recall failed, falling back to no-memory triage: {exc}")
        if include_reflection and result.memories:
            try:
                result.reflection = memory.reflect(
                    f"An alert just fired: '{alert}'. Based on past incidents, what most likely caused it, "
                    "what fixed it before, and what should the on-call engineer avoid?"
                )
            except MemoryError_ as exc:
                result.warnings.append(f"Reflect failed (recall results still used): {exc}")
        if not result.memories and not result.warnings:
            result.warnings.append("No similar past incidents found in memory.")

    has_memory = bool(result.memories)
    if llm is not None:
        user = f"New alert:\n{alert}\n\n"
        if has_memory:
            user += f"Relevant memories from past incidents (Hindsight recall):\n{_format_memories(result.memories)}\n\n"
            if result.reflection:
                user += f"Hindsight reflect summary:\n{result.reflection}\n\n"
        else:
            user += "No incident history is available. Give the best generic plan and say confidence is low.\n"
        try:
            result.plan = llm.complete(SYSTEM_PROMPT, user)
            result.llm_used = True
            return result
        except LLMError as exc:
            result.warnings.append(f"LLM unavailable, showing offline plan: {exc}")

    result.plan = _offline_memory_plan(result.memories, result.reflection) if has_memory else GENERIC_PLAN
    return result


_SLUG = re.compile(r"[^a-z0-9-]+")


def record_resolution(
    memory: IncidentMemory,
    *,
    title: str,
    service: str,
    severity: str,
    symptoms: str,
    root_cause: str,
    what_worked: str,
    what_failed: str = "",
    teams: list[str] | None = None,
    runbook: str = "",
    time_to_resolve_min: int | None = None,
) -> Incident:
    """Validate a postmortem and retain it so the next similar alert benefits."""
    required = {"title": title, "service": service, "root_cause": root_cause, "what_worked": what_worked}
    missing = [k for k, v in required.items() if not (v or "").strip()]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    if time_to_resolve_min is not None and time_to_resolve_min < 0:
        raise ValueError("time_to_resolve_min must be >= 0")
    service_slug = _SLUG.sub("-", service.strip().lower()).strip("-")
    inc = Incident(
        incident_id=f"INC-{utcnow():%Y%m%d}-{uuid.uuid4().hex[:4].upper()}",
        title=title.strip(),
        service=service_slug,
        severity=severity,
        occurred_at=utcnow(),
        symptoms=symptoms.strip(),
        root_cause=root_cause.strip(),
        what_worked=what_worked.strip(),
        what_failed=what_failed.strip(),
        teams=[t.strip().lower() for t in (teams or []) if t.strip()],
        runbook=runbook.strip(),
        time_to_resolve_min=time_to_resolve_min,
    )
    memory.retain_incident(inc)
    return inc
