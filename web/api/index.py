"""RecallOps web API (FastAPI) for Vercel Python functions.

Reuses the same agent / memory / llm modules as the Streamlit app and CLI.
Routes (all under /api): health, triage, resolve, explore, learning/*.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

# Core modules live at the repo root locally, or next to this file in the deploy bundle.
_HERE = Path(__file__).resolve().parent
for p in (_HERE, _HERE.parent, _HERE.parent.parent):
    if (p / "agent.py").exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from agent import record_resolution, triage  # noqa: E402
from config import load_settings  # noqa: E402
from incidents_data import DEMO_ALERTS  # noqa: E402
from llm import build_llm  # noqa: E402
from memory import HindsightIncidentMemory, InMemoryIncidentMemory, MemoryError_, build_memory  # noqa: E402
from incidents_data import seed_incidents  # noqa: E402
from demo_agent import SAMPLE_ALERTS, learn as agent_learn, parse_webhook, run_agent  # noqa: E402

app = FastAPI(title="RecallOps API", docs_url="/api/docs", openapi_url="/api/openapi.json")
settings = load_settings()

MAX_ALERT = 500
MAX_FIELD = 1200
# Best-effort per-IP limits (per warm instance). Public demo running on the owner's keys.
LIMITS = {"triage": (12, 600), "resolve": (4, 600), "explore": (20, 600), "learning": (10, 600), "agent": (30, 600)}
_hits: dict[tuple[str, str], deque] = defaultdict(deque)
_lock = threading.Lock()


def _client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() or (req.client.host if req.client else "unknown"))[:64]


def _rate_limit(req: Request, bucket: str) -> None:
    n, window = LIMITS[bucket]
    key, now = (_client_ip(req), bucket), time.time()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= n:
            raise HTTPException(429, f"Rate limit: max {n} {bucket} requests per {window // 60} min. Please wait a bit.")
        q.append(now)


_offline = None


def _memory(bank_id: str | None = None):
    global _offline
    if settings.hindsight_ready:
        return HindsightIncidentMemory(settings.hindsight_base_url, settings.hindsight_api_key, bank_id or settings.bank_id)
    if _offline is None:
        _offline = InMemoryIncidentMemory()
        _offline.retain_incidents(seed_incidents())
    return _offline


_CONF = re.compile(r"confidence\W{0,12}(?:\*\*)?\s*[-–—:]*\s*(?:\*\*)?\s*(low|medium|high)", re.I)


def _confidence(plan: str) -> str | None:
    m = _CONF.search(plan or "")
    return m.group(1).lower() if m else None


def _ser(res) -> dict:
    return {
        "plan": res.plan,
        "use_memory": res.use_memory,
        "confidence": _confidence(res.plan),
        "reflection": res.reflection,
        "warnings": res.warnings,
        "llm_used": res.llm_used,
        "memories": [
            {"text": m.text, "type": m.type, "occurred": m.occurred, "source": m.document_id, "score": m.score}
            for m in res.memories
        ],
        "sources": sorted({m.document_id for m in res.memories if m.document_id}),
    }


def _clean(s: str | None, limit: int) -> str:
    s = (s or "").strip()
    if len(s) > limit:
        raise HTTPException(422, f"Input too long (max {limit} characters).")
    return s


@app.exception_handler(MemoryError_)
async def _mem_err(_req, exc):  # pragma: no cover
    return JSONResponse(status_code=502, content={"detail": f"Memory service error: {exc}"})


@app.get("/api/health")
def health():
    return {
        "memory": "hindsight" if settings.hindsight_ready else "offline",
        "llm": settings.groq_model if settings.llm_ready else None,
        "bank": settings.bank_id,
        "examples": DEMO_ALERTS,
    }


class TriageIn(BaseModel):
    alert: str = Field(..., max_length=MAX_ALERT * 2)
    use_memory: bool = True
    reflect: bool = True


@app.post("/api/triage")
def api_triage(body: TriageIn, request: Request):
    _rate_limit(request, "triage")
    alert = _clean(body.alert, MAX_ALERT)
    if not alert:
        raise HTTPException(422, "Alert text is empty.")
    mem = _memory()
    try:
        t = time.time()
        res = triage(alert, mem, build_llm(settings), use_memory=body.use_memory, include_reflection=body.reflect)
        out = _ser(res)
        out["seconds"] = round(time.time() - t, 1)
        return out
    finally:
        mem.close()


class ResolveIn(BaseModel):
    title: str
    service: str
    severity: str = "SEV2"
    symptoms: str = ""
    root_cause: str
    what_worked: str
    what_failed: str = ""
    teams: str = ""
    runbook: str = ""
    time_to_resolve_min: int | None = Field(default=None, ge=0, le=10000)


def _resolve(body: ResolveIn, mem):
    if body.severity not in {"SEV1", "SEV2", "SEV3", "SEV4"}:
        raise HTTPException(422, "Severity must be SEV1-SEV4.")
    f = {k: _clean(getattr(body, k), 120 if k in ("title", "service", "runbook", "teams") else MAX_FIELD)
         for k in ("title", "service", "symptoms", "root_cause", "what_worked", "what_failed", "teams", "runbook")}
    try:
        inc = record_resolution(
            mem, title=f["title"], service=f["service"], severity=body.severity, symptoms=f["symptoms"],
            root_cause=f["root_cause"], what_worked=f["what_worked"], what_failed=f["what_failed"],
            teams=f["teams"].split(","), runbook=f["runbook"], time_to_resolve_min=body.time_to_resolve_min,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"incident_id": inc.incident_id, "memory_text": inc.to_memory_text(), "tags": inc.tags(),
            "timestamp": inc.occurred_at.isoformat()}


@app.post("/api/resolve")
def api_resolve(body: ResolveIn, request: Request):
    _rate_limit(request, "resolve")
    mem = _memory()
    try:
        return _resolve(body, mem)
    finally:
        mem.close()


@app.get("/api/explore")
def api_explore(request: Request, q: str = "", limit: int = 60):
    _rate_limit(request, "explore")
    q = _clean(q, 300)
    mem = _memory()
    try:
        rows = mem.recall_similar(q) if q else mem.list_recent(limit=max(1, min(limit, 100)))
        return {"query": q, "memories": [
            {"text": m.text, "type": m.type, "occurred": m.occurred, "source": m.document_id} for m in rows]}
    finally:
        mem.close()


# ---------- Learning curve: a private sandbox bank per visitor ----------
_BANK_RE = re.compile(r"^[a-f0-9]{10}$")


def _sandbox(sid: str):
    if not _BANK_RE.match(sid or ""):
        raise HTTPException(422, "Bad sandbox id.")
    return _memory(f"recallops-lc-{sid}")


@app.post("/api/learning/start")
def lc_start(request: Request):
    _rate_limit(request, "learning")
    sid = uuid.uuid4().hex[:10]
    mem = _sandbox(sid)
    try:
        mem.ensure_bank()  # create the empty bank so the first recall is a clean "no memory"
    finally:
        mem.close()
    return {"sandbox": sid}


class LCTriageIn(BaseModel):
    sandbox: str
    alert: str


@app.post("/api/learning/triage")
def lc_triage(body: LCTriageIn, request: Request):
    _rate_limit(request, "learning")
    alert = _clean(body.alert, MAX_ALERT)
    mem = _sandbox(body.sandbox)
    try:
        t = time.time()
        res = triage(alert, mem, build_llm(settings), use_memory=True, include_reflection=False)
        out = _ser(res)
        out["seconds"] = round(time.time() - t, 1)
        return out
    finally:
        mem.close()


class LCResolveIn(ResolveIn):
    sandbox: str


@app.post("/api/learning/resolve")
def lc_resolve(body: LCResolveIn, request: Request):
    _rate_limit(request, "learning")
    mem = _sandbox(body.sandbox)
    try:
        return _resolve(body, mem)
    finally:
        mem.close()


# ---------- Live agent loop: simulate -> recall -> reason -> act -> learn ----------
@app.get("/api/agent/samples")
def agent_samples():
    return {"samples": SAMPLE_ALERTS}


@app.get("/api/agent/simulate")
def agent_simulate(key: str = ""):
    pool = [s for s in SAMPLE_ALERTS if s["key"] == key] or SAMPLE_ALERTS
    import random

    return random.choice(pool)


class LearnedIn(BaseModel):
    id: str = Field(..., max_length=60)
    title: str = Field("", max_length=200)
    service: str = Field("", max_length=80)
    severity: str = Field("SEV2", max_length=8)
    symptoms: str = Field("", max_length=MAX_FIELD)
    root_cause: str = Field("", max_length=MAX_FIELD)
    what_worked: str = Field("", max_length=MAX_FIELD)
    what_failed: str = Field("", max_length=MAX_FIELD)
    ttr: int | None = Field(default=None, ge=0, le=10000)


class AgentRunIn(BaseModel):
    alert: str = Field(..., max_length=MAX_ALERT * 2)
    learned: list[LearnedIn] = Field(default_factory=list, max_length=10)


def _agent_run(alert: str, learned: list[dict]):
    alert = _clean(alert, MAX_ALERT)
    if not alert:
        raise HTTPException(422, "Alert text is empty.")
    mem = _memory()
    try:
        backend = "hindsight" if settings.hindsight_ready else "offline"
        return run_agent(alert, mem, build_llm(settings), learned=learned, memory_backend=backend).to_dict()
    finally:
        mem.close()


@app.post("/api/agent/run")
def agent_run(body: AgentRunIn, request: Request):
    _rate_limit(request, "agent")
    return _agent_run(body.alert, [l.model_dump() for l in body.learned])


@app.post("/api/agent/webhook")
async def agent_webhook(request: Request):
    """Alert webhook: plain {"alert": "..."}, Prometheus Alertmanager or PagerDuty-style JSON."""
    _rate_limit(request, "agent")
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, "Body must be JSON.") from exc
    return _agent_run(parse_webhook(payload), [])


class AgentLearnIn(BaseModel):
    title: str = Field(..., max_length=120)
    service: str = Field(..., max_length=80)
    severity: str = "SEV2"
    symptoms: str = Field("", max_length=MAX_FIELD)
    root_cause: str = Field(..., max_length=MAX_FIELD)
    what_worked: str = Field(..., max_length=MAX_FIELD)
    what_failed: str = Field("", max_length=MAX_FIELD)
    teams: str = Field("", max_length=120)
    runbook: str = Field("", max_length=120)
    time_to_resolve_min: int | None = Field(default=None, ge=0, le=10000)


@app.post("/api/agent/learn")
def agent_learn_ep(body: AgentLearnIn, request: Request):
    _rate_limit(request, "resolve")
    if body.severity not in {"SEV1", "SEV2", "SEV3", "SEV4"}:
        raise HTTPException(422, "Severity must be SEV1-SEV4.")
    mem = _memory()
    try:
        return agent_learn(mem, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        mem.close()


# Local dev: `RECALLOPS_SERVE_STATIC=1 uvicorn api.index:app` also serves the frontend.
# On Vercel, public/ is served by the CDN and only /api/* reaches this function.
if os.getenv("RECALLOPS_SERVE_STATIC") == "1":
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_HERE.parent / "public"), html=True), name="static")
