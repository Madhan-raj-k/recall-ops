import json

import pytest

import workflow as wf
from incidents_data import seed_incidents
from llm import LLMError
from memory import InMemoryIncidentMemory, MemoryError_

ALERT = "Datadog SEV2: API latency spike on payments-api, p95 4.8s on /v2/charge, DB pool wait time rising"


class SpyMemory(InMemoryIncidentMemory):
    def __init__(self):
        super().__init__()
        self.retain_incidents(seed_incidents())
        self.recall_calls = []

    def recall_similar(self, query, max_tokens=2048):
        self.recall_calls.append(query)
        return super().recall_similar(query, max_tokens)


class ExplodingMemory(InMemoryIncidentMemory):
    def recall_similar(self, query, max_tokens=2048):
        raise AssertionError("memory must not be touched")

    def retain_incident(self, incident):
        raise MemoryError_("hindsight down")


class FakeLLM:
    def __init__(self, payload=None, fail=False):
        self.payload, self.fail, self.users = payload, fail, []

    def complete(self, system, user):
        self.users.append(user)
        if self.fail:
            raise LLMError("groq down")
        return json.dumps(self.payload)


def _approved_verified(action):
    t = wf.transition(action["token"], "approve")["token"]
    t = wf.transition(t, "simulate")["token"]
    return wf.transition(t, "verify", [{"check": "p95 back to baseline", "passed": True}])


def test_memory_off_does_not_touch_memory():
    out = wf.investigate(ALERT, ExplodingMemory(), None, use_memory=False)
    assert out["memory_recall_called"] is False
    assert out["historical_incidents"] == []
    assert any("Memory OFF" in m for m in out["investigation"]["missing_information"])
    assert all(a["evidence_type"] == wf.GENERAL and a["incident_id"] is None for a in out["investigation"]["actions"])


def test_memory_on_retrieves_real_history_with_same_input():
    mem = SpyMemory()
    on = wf.investigate(ALERT, mem, None, use_memory=True)
    off = wf.investigate(ALERT, mem, None, use_memory=False)
    assert mem.recall_calls == [ALERT]  # exactly one recall, from ON, with the identical input
    assert on["alert"] == off["alert"] == ALERT
    ids = {s.incident_id for s in seed_incidents()}
    assert 1 <= len(on["historical_incidents"]) <= 3
    assert all(h["id"] in ids for h in on["historical_incidents"])
    rows = wf.compare_rows(off, on)
    assert rows[1]["off"] == "None retrieved" and rows[1]["on"] != "None retrieved"


def test_llm_invented_references_are_stripped():
    payload = {"summary": "s", "service": "payments-api", "symptoms": ["slow"],
               "potential_root_causes": [{"cause": "c", "basis": "evidence", "incident_ids": ["INC-9999"]}],
               "supporting_evidence": [{"claim": "made up", "incident_id": "INC-9999"}],
               "missing_information": [], "next_steps": [], "previous_failures": [{"attempt": "x", "incident_id": "INC-9999"}],
               "actions": [{"title": "Check pool metrics", "incident_id": "INC-9999", "historical_outcome": "successful", "risk": "low"},
                           {"title": "Roll back the deploy", "incident_id": None, "risk": "low", "historical_outcome": "successful"}]}
    out = wf.investigate(ALERT, SpyMemory(), FakeLLM(payload), use_memory=True)
    inv = out["investigation"]
    assert out["llm_used"]
    assert inv["supporting_evidence"] == [] and inv["previous_failures"] == []
    assert inv["potential_root_causes"][0]["basis"] == "assumption"
    a0, a1 = inv["actions"]
    assert a0["incident_id"] is None and a0["evidence_type"] == wf.GENERAL and a0["historical_outcome"] == "unknown"
    assert a1["risk"] == "high"  # model cannot downgrade a rollback
    assert a1["historical_outcome"] == "unknown"
    assert inv["validation_notes"]


def test_missing_evidence_is_honest_and_llm_failure_falls_back():
    empty = InMemoryIncidentMemory()
    out = wf.investigate("printer on floor 3 is jammed", empty, FakeLLM(fail=True), use_memory=True)
    inv = out["investigation"]
    assert out["historical_incidents"] == [] and not out["llm_used"] and out["warnings"]
    assert any("No similar past incidents" in m for m in inv["missing_information"])
    assert inv["supporting_evidence"] == []


def test_empty_input_rejected():
    with pytest.raises(ValueError):
        wf.investigate("  ", None, None, use_memory=False)


def test_approval_state_machine():
    out = wf.investigate(ALERT, SpyMemory(), None, use_memory=True)
    acts = out["investigation"]["actions"]
    assert all(a["status"] == "awaiting_approval" and a["requires_approval"] for a in acts)
    high = next(a for a in acts if a["risk"] == "high") if any(a["risk"] == "high" for a in acts) else acts[-1]
    with pytest.raises(ValueError):  # cannot execute before approval
        wf.transition(high["token"], "simulate")
    rej = wf.transition(high["token"], "reject")
    assert rej["status"] == "rejected"
    for op in ("simulate", "approve", "verify"):
        with pytest.raises(ValueError):
            wf.transition(rej["token"], op)
    ap = wf.transition(acts[0]["token"], "approve")
    assert ap["status"] == "approved" and ap["resolved"] is False
    with pytest.raises(ValueError):  # approval alone cannot verify/resolve
        wf.transition(ap["token"], "verify", [{"check": "x", "passed": True}])
    sim = wf.transition(ap["token"], "simulate")
    assert sim["simulated"] is True and sim["real_infrastructure_changed"] is False
    assert all(line.startswith("[SIMULATION]") for line in sim["log"]) and sim["resolved"] is False
    with pytest.raises(ValueError):
        wf.transition(sim["token"], "verify", [])
    bad = wf.transition(sim["token"], "verify", [{"check": "errors normal", "passed": False}])
    assert bad["status"] == "verified_failed" and not bad["resolved"]
    good = wf.transition(sim["token"], "verify", [{"check": "errors normal", "passed": True}])
    assert good["status"] == "verified_passed" and good["resolved"]


def test_forged_token_rejected():
    out = wf.investigate(ALERT, None, None, use_memory=False)
    tok = out["investigation"]["actions"][0]["token"]
    b64, mac = tok.rsplit(".", 1)
    with pytest.raises(ValueError):
        wf.transition(b64 + "." + "0" * 32, "approve")


def _pm(test=True):
    return wf.PostmortemIn(alert=ALERT, title="Payments latency from pool starvation", service="payments-api",
                           severity="SEV2", root_cause="Batch job held DB pool connections",
                           lessons="Isolate batch pools", prevention="Alert on pool wait time", test=test)


def test_postmortem_requires_verified_action():
    out = wf.investigate(ALERT, None, None, use_memory=False)
    tok = wf.transition(out["investigation"]["actions"][0]["token"], "approve")["token"]
    with pytest.raises(ValueError):
        wf.build_postmortem(_pm(), [tok])


def test_retain_success_duplicate_and_recall():
    mem = SpyMemory()
    out = wf.investigate(ALERT, mem, None, use_memory=True)
    v = _approved_verified(out["investigation"]["actions"][0])
    inc, doc = wf.build_postmortem(_pm(), [v["token"]])
    assert "SIMULATED" in doc["actions_taken"] and "No real infrastructure" in doc["result"]
    assert "test" in inc.tags()
    wf._RETAINED.clear()
    r1 = wf.retain_postmortem(mem, inc)
    assert r1["retained"] is True
    r2 = wf.retain_postmortem(mem, inc)
    assert r2["retained"] is False and r2["duplicate"] is True
    assert sum(1 for i in mem.incidents if i.incident_id == inc.incident_id) == 1
    chk = wf.check_recall(mem, "payments-api p95 latency 5s again, DB pool wait high", inc.incident_id)
    assert chk["found"] is True and chk["rank"] is not None
    again = wf.investigate("payments-api p95 latency 5s again, DB pool wait high", mem, None, use_memory=True)
    assert inc.incident_id in [h["id"] for h in again["historical_incidents"]] or chk["rank"] > 3


def test_failed_retain_not_reported_as_success():
    out = wf.investigate(ALERT, None, None, use_memory=False)
    v = _approved_verified(out["investigation"]["actions"][0])
    inc, _ = wf.build_postmortem(_pm(), [v["token"]])
    wf._RETAINED.clear()
    r = wf.retain_postmortem(ExplodingMemory(), inc)
    assert r["retained"] is False and "FAILED" in r["message"] and r["error"]
    assert inc.incident_id not in wf._RETAINED
