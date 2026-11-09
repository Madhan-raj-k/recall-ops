from types import SimpleNamespace

try:  # openai>=3 ships on httpx2
    import httpx2 as httpx
except ImportError:  # openai 1.x/2.x
    import httpx
import openai
import pytest

import llm as llm_mod
from llm import GroqLLM, LLMError


def _resp(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def create(self, **kw):
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def client(outcomes):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(outcomes)))


REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def test_strips_think_tags():
    g = GroqLLM("k", "m", client=client([_resp("<think>hmm</think>\nPlan")]))
    assert g.complete("s", "u") == "Plan"


def test_retries_rate_limit(monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    rl = openai.RateLimitError("slow down", response=httpx.Response(429, request=REQ), body=None)
    g = GroqLLM("k", "m", client=client([rl, _resp("ok")]))
    assert g.complete("s", "u") == "ok"


def test_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    errs = [openai.APIConnectionError(request=REQ) for _ in range(3)]
    with pytest.raises(LLMError, match="after 3 attempts"):
        GroqLLM("k", "m", client=client(errs)).complete("s", "u")


def test_auth_error_is_clear():
    err = openai.AuthenticationError("bad key", response=httpx.Response(401, request=REQ), body=None)
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        GroqLLM("k", "m", client=client([err])).complete("s", "u")


def test_empty_output_is_error():
    with pytest.raises(LLMError):
        GroqLLM("k", "m", client=client([_resp("   ")]), max_retries=1).complete("s", "u")
