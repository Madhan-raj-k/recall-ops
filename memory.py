"""Hindsight memory layer for RecallOps.

Everything RecallOps knows about past incidents lives in one Hindsight memory bank.
This module is the only place that talks to Hindsight, and it exposes three verbs that
map 1:1 onto the Hindsight API:

* ``retain_incident``  -> ``Hindsight.retain``  (store a postmortem)
* ``recall_similar``   -> ``Hindsight.recall``  (find related past incidents)
* ``reflect``          -> ``Hindsight.reflect`` (let the bank reason over its own history)

``InMemoryIncidentMemory`` implements the same interface without a server so the app,
tests and demo run offline.
"""
from __future__ import annotations

import logging
import re
from typing import Protocol

from models import Incident, RecalledMemory

log = logging.getLogger(__name__)

RETAIN_MISSION = (
    "You are the long-term memory of an e-commerce on-call team. From each incident postmortem, "
    "extract: the affected service, the symptoms and alerts, the root cause, the fix that actually "
    "worked, any attempted fix that did NOT work, the teams and runbooks involved, and time to resolve. "
    "Keep exact technical names (services, metrics, error codes, config keys)."
)
REFLECT_MISSION = (
    "You are a senior SRE advising the on-call engineer. Ground every recommendation in past incidents "
    "from memory, cite the incident IDs you rely on, prefer fixes that worked before, and explicitly warn "
    "against fixes that failed before. If memory has nothing relevant, say so plainly."
)


class MemoryError_(RuntimeError):
    """Raised when the memory backend is unreachable or rejects a request."""


class IncidentMemory(Protocol):
    name: str

    def ensure_bank(self) -> None: ...

    def retain_incident(self, incident: Incident) -> None: ...

    def retain_incidents(self, incidents: list[Incident]) -> int: ...

    def recall_similar(self, query: str, max_tokens: int = 2048) -> list[RecalledMemory]: ...

    def reflect(self, query: str) -> str: ...

    def list_recent(self, limit: int = 50) -> list[RecalledMemory]: ...


def _item(incident: Incident) -> dict:
    return {
        "content": incident.to_memory_text(),
        "timestamp": incident.occurred_at,
        "context": f"postmortem for {incident.service} incident",
        "document_id": incident.incident_id,
        "metadata": incident.metadata(),
        "tags": incident.tags(),
    }


class HindsightIncidentMemory:
    """Thin, defensive wrapper around ``hindsight_client.Hindsight``."""

    name = "Hindsight"
    _configured_banks: set[str] = set()  # process-wide: configure each bank once

    def __init__(self, base_url: str, api_key: str | None, bank_id: str, client=None, timeout: float = 120.0):
        if client is None:
            from hindsight_client import Hindsight

            client = Hindsight(base_url=base_url, api_key=api_key, timeout=timeout, user_agent="recall-ops/0.1")
        self._client = client
        self.bank_id = bank_id
        self._bank_ready = False

    # -- bank -------------------------------------------------------------
    def ensure_bank(self) -> None:
        """Create/update the bank with missions that steer extraction and reasoning.

        Banks are also created on first use, so a failure here is logged, not fatal.
        """
        if self._bank_ready or self.bank_id in self._configured_banks:
            return
        try:
            self._client.create_bank(
                bank_id=self.bank_id,
                retain_mission=RETAIN_MISSION,
                reflect_mission=REFLECT_MISSION,
            )
        except Exception as exc:  # noqa: BLE001 - bank config is best-effort
            log.warning("Could not configure bank %s (continuing): %s", self.bank_id, exc)
        else:
            self._configured_banks.add(self.bank_id)
        self._bank_ready = True

    # -- retain -----------------------------------------------------------
    def retain_incident(self, incident: Incident) -> None:
        self.retain_incidents([incident])

    def retain_incidents(self, incidents: list[Incident]) -> int:
        if not incidents:
            return 0
        self.ensure_bank()
        try:
            resp = self._client.retain_batch(bank_id=self.bank_id, items=[_item(i) for i in incidents])
        except Exception as exc:  # noqa: BLE001
            raise MemoryError_(f"Hindsight retain failed: {exc}") from exc
        return int(getattr(resp, "items_count", None) or len(incidents))

    # -- recall -----------------------------------------------------------
    def recall_similar(self, query: str, max_tokens: int = 2048) -> list[RecalledMemory]:
        if not query.strip():
            return []
        try:
            resp = self._client.recall(
                bank_id=self.bank_id,
                query=query,
                types=["world", "experience", "observation"],
                max_tokens=max_tokens,
                budget="mid",
            )
        except Exception as exc:  # noqa: BLE001
            raise MemoryError_(f"Hindsight recall failed: {exc}") from exc
        out: list[RecalledMemory] = []
        for r in getattr(resp, "results", None) or []:
            scores = getattr(r, "scores", None)
            out.append(
                RecalledMemory(
                    text=r.text,
                    type=r.type or "world",
                    occurred=r.occurred_start or r.mentioned_at,
                    tags=list(r.tags or []),
                    document_id=r.document_id,
                    score=getattr(scores, "final", None) if scores else None,
                )
            )
        return out

    # -- reflect ----------------------------------------------------------
    def reflect(self, query: str) -> str:
        try:
            resp = self._client.reflect(bank_id=self.bank_id, query=query, budget="low")
        except Exception as exc:  # noqa: BLE001
            raise MemoryError_(f"Hindsight reflect failed: {exc}") from exc
        return (getattr(resp, "text", "") or "").strip()

    # -- explorer ---------------------------------------------------------
    def list_recent(self, limit: int = 50) -> list[RecalledMemory]:
        try:
            resp = self._client.list_memories(bank_id=self.bank_id, limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise MemoryError_(f"Hindsight list_memories failed: {exc}") from exc
        return [
            RecalledMemory(
                text=i.text or "",
                type=i.fact_type or "world",
                occurred=i.occurred_start or i.mentioned_at,
                tags=list(i.tags or []),
                document_id=i.document_id,
            )
            for i in resp.items
        ]


_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "are", "with", "after", "at", "by"}


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


class InMemoryIncidentMemory:
    """Offline stand-in for Hindsight (keyword overlap). Used for tests and keyless demos."""

    name = "Offline (in-memory)"

    def __init__(self) -> None:
        self.incidents: list[Incident] = []

    def ensure_bank(self) -> None:
        return None

    def retain_incident(self, incident: Incident) -> None:
        self.incidents.append(incident)

    def retain_incidents(self, incidents: list[Incident]) -> int:
        self.incidents.extend(incidents)
        return len(incidents)

    def recall_similar(self, query: str, max_tokens: int = 2048) -> list[RecalledMemory]:
        q = _tokens(query)
        if not q:
            return []
        scored = []
        for inc in self.incidents:
            overlap = len(q & _tokens(inc.to_memory_text() + " " + inc.service.replace("-", " ")))
            if overlap:
                scored.append((overlap / len(q), inc))
        scored.sort(key=lambda s: (s[0], s[1].occurred_at), reverse=True)
        budget, out = max_tokens * 4, []  # ~4 chars per token
        for score, inc in scored[:5]:
            text = inc.to_memory_text()
            if budget - len(text) < 0 and out:
                break
            budget -= len(text)
            out.append(
                RecalledMemory(
                    text=text,
                    type="world",
                    occurred=inc.occurred_at.isoformat(),
                    tags=inc.tags(),
                    document_id=inc.incident_id,
                    score=round(score, 3),
                )
            )
        return out

    def reflect(self, query: str) -> str:
        hits = self.recall_similar(query)
        if not hits:
            return "No relevant past incidents in memory."
        return "Most relevant past incidents: " + ", ".join(h.document_id or "?" for h in hits)

    def list_recent(self, limit: int = 50) -> list[RecalledMemory]:
        incs = sorted(self.incidents, key=lambda i: i.occurred_at, reverse=True)[:limit]
        return [
            RecalledMemory(text=i.to_memory_text(), occurred=i.occurred_at.isoformat(), tags=i.tags(), document_id=i.incident_id)
            for i in incs
        ]


def build_memory(settings) -> IncidentMemory:
    """Return a Hindsight-backed memory when configured, otherwise the offline fallback."""
    if settings.hindsight_ready:
        return HindsightIncidentMemory(settings.hindsight_base_url, settings.hindsight_api_key, settings.bank_id)
    return InMemoryIncidentMemory()
