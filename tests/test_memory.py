from types import SimpleNamespace

import pytest

from incidents_data import seed_incidents
from memory import HindsightIncidentMemory, MemoryError_


class FakeHindsight:
    """Records calls with the same signatures as hindsight_client.Hindsight."""

    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    def _maybe_fail(self, name):
        if self.fail == name:
            raise RuntimeError("503 at capacity")

    def create_bank(self, bank_id, **kw):
        self.calls.append(("create_bank", bank_id, kw))
        self._maybe_fail("create_bank")

    def retain_batch(self, bank_id, items, **kw):
        self.calls.append(("retain_batch", bank_id, items))
        self._maybe_fail("retain_batch")
        return SimpleNamespace(items_count=len(items), success=True)

    def recall(self, bank_id, query, **kw):
        self.calls.append(("recall", bank_id, query, kw))
        self._maybe_fail("recall")
        r = SimpleNamespace(text="Rollback fixed payments-api 5xx", type="world", occurred_start="2026-10-01T00:00:00Z",
                            mentioned_at=None, tags=["service:payments-api"], document_id="INC-1001",
                            scores=SimpleNamespace(final=0.91))
        return SimpleNamespace(results=[r])

    def reflect(self, bank_id, query, **kw):
        self.calls.append(("reflect", bank_id, query, kw))
        self._maybe_fail("reflect")
        return SimpleNamespace(text=" Roll back first. ")

    def list_memories(self, bank_id, limit=100, **kw):
        item = SimpleNamespace(text="fact", fact_type="world", occurred_start=None, mentioned_at="2026-10-02", tags=[], document_id="INC-1")
        return SimpleNamespace(items=[item], total=1)


def make(fail=None, bank="bank-test"):
    HindsightIncidentMemory._configured_banks.discard(bank)
    fake = FakeHindsight(fail)
    return HindsightIncidentMemory("http://x", None, bank, client=fake), fake


def test_retain_sends_structured_items():
    mem, fake = make()
    incs = seed_incidents()[:3]
    assert mem.retain_incidents(incs) == 3
    name, bank, items = fake.calls[-1]
    assert name == "retain_batch" and bank == "bank-test"
    first = items[0]
    assert first["document_id"] == incs[0].incident_id
    assert first["timestamp"] == incs[0].occurred_at
    assert "service:payments-api" in first["tags"] and "Root cause:" in first["content"]
    assert fake.calls[0][0] == "create_bank" and "retain_mission" in fake.calls[0][2]


def test_bank_configuration_failure_is_not_fatal():
    mem, fake = make(fail="create_bank")
    assert mem.retain_incidents(seed_incidents()[:1]) == 1


def test_recall_normalises_results():
    mem, fake = make()
    [m] = mem.recall_similar("payments 5xx")
    assert m.document_id == "INC-1001" and m.score == 0.91 and m.occurred.startswith("2026-10-01")
    assert fake.calls[-1][3]["budget"] == "mid"


def test_recall_blank_query_skips_api():
    mem, fake = make()
    assert mem.recall_similar("  ") == [] and fake.calls == []


@pytest.mark.parametrize("op", ["recall", "reflect", "retain_batch"])
def test_errors_are_wrapped(op):
    mem, _ = make(fail=op)
    with pytest.raises(MemoryError_):
        {"recall": lambda: mem.recall_similar("q"), "reflect": lambda: mem.reflect("q"),
         "retain_batch": lambda: mem.retain_incidents(seed_incidents()[:1])}[op]()


def test_reflect_and_list():
    mem, _ = make()
    assert mem.reflect("q") == "Roll back first."
    assert mem.list_recent()[0].occurred == "2026-10-02"
