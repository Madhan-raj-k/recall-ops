from demo_agent import SAMPLE_ALERTS, learn, parse_webhook, run_agent
from incidents_data import seed_incidents
from memory import InMemoryIncidentMemory


def _mem():
    m = InMemoryIncidentMemory()
    m.retain_incidents(seed_incidents())
    return m


def test_full_loop_offline_learns_and_takes_fast_path():
    mem = _mem()
    s = SAMPLE_ALERTS[0]
    first = run_agent(s["alert"], mem, None)
    assert not first.fast_path
    assert 1 <= len(first.matches) <= 3
    assert "**Reasoning**" in first.plan
    assert first.action["mode"] == "dry-run"
    assert [st["name"] for st in first.steps] == ["alert received", "recall", "reason", "act"]

    out = learn(mem, service=s["service"], severity=s["severity"], **s["resolution"])
    assert out["stored"] and out["learned"]["id"].startswith("INC-")

    second = run_agent(s["followup"], mem, None, learned=[out["learned"]])
    assert second.fast_path
    assert second.matches[0]["id"] == out["learned"]["id"]
    assert second.est_ttr_min < first.est_ttr_min


def test_all_samples_followups_hit_learned():
    for s in SAMPLE_ALERTS:
        rec = dict(id="INC-X", service=s["service"], **{k: v for k, v in s["resolution"].items()
                                                        if k in ("title", "symptoms", "root_cause", "what_worked")})
        assert run_agent(s["followup"], _mem(), None, learned=[rec]).fast_path, s["key"]
        assert not run_agent(s["alert"], _mem(), None).fast_path


def test_parse_webhook_formats():
    assert parse_webhook({"alert": "x"}) == "x"
    am = {"alerts": [{"labels": {"alertname": "HighLatency", "service": "payments-api"}, "annotations": {"summary": "p95 5s"}}]}
    assert "payments-api" in parse_webhook(am)
    assert parse_webhook({"event": {"data": {"title": "checkout 5xx"}}}) == "checkout 5xx"
