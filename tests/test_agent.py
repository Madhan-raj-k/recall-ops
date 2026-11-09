import pytest

from agent import GENERIC_PLAN, record_resolution, triage
from incidents_data import seed_incidents
from llm import LLMError
from memory import InMemoryIncidentMemory, MemoryError_


class FakeLLM:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def complete(self, system, user):
        self.calls.append(user)
        if self.fail:
            raise LLMError("boom")
        return "PLAN"


class BrokenMemory(InMemoryIncidentMemory):
    def recall_similar(self, query, max_tokens=2048):
        raise MemoryError_("down")


@pytest.fixture
def mem():
    m = InMemoryIncidentMemory()
    m.retain_incidents(seed_incidents())
    return m


ALERT = "payments-api 5xx on /v2/charge right after deploy"


def test_seed_has_realistic_volume_and_is_labeled():
    incs = seed_incidents()
    assert 15 <= len(incs) <= 20
    assert all(i.synthetic and "synthetic" in i.tags() for i in incs)
    assert len({i.incident_id for i in incs}) == len(incs)
    assert len({i.occurred_at.date() for i in incs}) == len(incs)  # spread over time


def test_memory_off_uses_no_history(mem):
    llm = FakeLLM()
    res = triage(ALERT, mem, llm, use_memory=False)
    assert res.memories == [] and res.plan == "PLAN"
    assert "Relevant memories" not in llm.calls[0]


def test_memory_on_injects_recalled_incidents(mem):
    llm = FakeLLM()
    res = triage(ALERT, mem, llm, use_memory=True)
    assert res.memories and res.memories[0].document_id.startswith("INC-")
    assert "Relevant memories" in llm.calls[0] and "INC-1001" in llm.calls[0]


def test_offline_plan_lists_what_worked_and_failed(mem):
    res = triage(ALERT, mem, None, use_memory=True)
    assert "argocd app rollback" in res.plan
    assert "Avoid" in res.plan and "Scaling" in res.plan


def test_llm_failure_falls_back_gracefully(mem):
    res = triage(ALERT, mem, FakeLLM(fail=True), use_memory=True)
    assert not res.llm_used and any("LLM unavailable" in w for w in res.warnings)
    assert "argocd" in res.plan


def test_memory_failure_falls_back_to_generic():
    res = triage(ALERT, BrokenMemory(), None, use_memory=True)
    assert res.plan == GENERIC_PLAN and any("recall failed" in w for w in res.warnings)


def test_empty_alert_rejected(mem):
    with pytest.raises(ValueError):
        triage("   ", mem, None)


def test_no_match_warns():
    res = triage("zzqx unrelated", InMemoryIncidentMemory(), None, use_memory=True)
    assert res.plan == GENERIC_PLAN and any("No similar" in w for w in res.warnings)


def test_record_resolution_improves_next_triage():
    m = InMemoryIncidentMemory()
    alert = "loyalty-points service returns 500 when tier recalculation job runs"
    assert triage(alert, m, None).memories == []
    inc = record_resolution(
        m, title="Loyalty 500s during tier job", service="Loyalty Points", severity="SEV2",
        symptoms="500 on /points", root_cause="tier recalculation job locked the points table",
        what_worked="moved the job to the replica and batched updates", what_failed="restarting pods",
        teams=["loyalty", " "], time_to_resolve_min=25,
    )
    assert inc.service == "loyalty-points" and inc.teams == ["loyalty"]
    after = triage(alert, m, None)
    assert after.memories and after.memories[0].document_id == inc.incident_id
    assert "batched updates" in after.plan


def test_record_resolution_validates():
    with pytest.raises(ValueError, match="root_cause"):
        record_resolution(InMemoryIncidentMemory(), title="t", service="s", severity="SEV3", symptoms="",
                          root_cause=" ", what_worked="x")
    with pytest.raises(ValueError):
        record_resolution(InMemoryIncidentMemory(), title="t", service="s", severity="SEV3", symptoms="",
                          root_cause="c", what_worked="x", time_to_resolve_min=-1)


def test_prompt_keeps_only_top_memories():
    from agent import MAX_PROMPT_MEMORIES
    from models import RecalledMemory

    class Many(InMemoryIncidentMemory):
        def recall_similar(self, query, max_tokens=2048):
            return [RecalledMemory(text=f"fact-{i}", document_id=f"INC-{i}") for i in range(80)]

    llm = FakeLLM()
    res = triage(ALERT, Many(), llm, use_memory=True, include_reflection=False)
    assert len(res.memories) == 80
    assert f"fact-{MAX_PROMPT_MEMORIES - 1}" in llm.calls[0] and f"fact-{MAX_PROMPT_MEMORIES}\n" not in llm.calls[0]
