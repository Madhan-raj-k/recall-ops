"""Plain data types shared by the memory layer, the agent and the UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Incident:
    """A resolved incident / postmortem that gets retained into Hindsight."""

    incident_id: str
    title: str
    service: str
    severity: str  # SEV1..SEV4
    occurred_at: datetime
    symptoms: str
    root_cause: str
    what_worked: str
    what_failed: str = ""
    teams: list[str] = field(default_factory=list)
    runbook: str = ""
    time_to_resolve_min: int | None = None
    synthetic: bool = False

    def to_memory_text(self) -> str:
        """Render the postmortem as a narrative Hindsight can extract facts from."""
        lines = [
            f"Incident {self.incident_id} ({self.severity}) on service {self.service}: {self.title}.",
            f"Symptoms observed: {self.symptoms}",
            f"Root cause: {self.root_cause}",
            f"Fix that worked: {self.what_worked}",
        ]
        if self.what_failed:
            lines.append(f"Attempts that did NOT work (avoid next time): {self.what_failed}")
        if self.teams:
            lines.append(f"Teams involved: {', '.join(self.teams)}.")
        if self.runbook:
            lines.append(f"Runbook used: {self.runbook}.")
        if self.time_to_resolve_min is not None:
            lines.append(f"Time to resolve: {self.time_to_resolve_min} minutes.")
        if self.synthetic:
            lines.append("(Synthetic demo data.)")
        return "\n".join(lines)

    def tags(self) -> list[str]:
        tags = [f"service:{self.service}", f"severity:{self.severity.lower()}", "kind:postmortem"]
        tags += [f"team:{t}" for t in self.teams]
        if self.synthetic:
            tags.append("synthetic")
        return tags

    def metadata(self) -> dict[str, str]:
        return {
            "incident_id": self.incident_id,
            "service": self.service,
            "severity": self.severity,
            "title": self.title,
        }


@dataclass
class RecalledMemory:
    """One memory returned by recall, normalised for display."""

    text: str
    type: str = "world"
    occurred: str | None = None
    tags: list[str] = field(default_factory=list)
    document_id: str | None = None
    score: float | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
