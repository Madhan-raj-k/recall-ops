"""Groq chat completions (OpenAI-compatible) with retries and clean error reporting."""
from __future__ import annotations

import logging
import re
import time
from typing import Protocol

from config import GROQ_BASE_URL

log = logging.getLogger(__name__)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(RuntimeError):
    """The LLM could not produce an answer (auth, quota, network, empty output)."""


class LLM(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class GroqLLM:
    def __init__(self, api_key: str, model: str, max_retries: int = 3, timeout: float = 60.0, client=None):
        if client is None:
            from openai import OpenAI

            # We do our own retries so we can log and back off predictably.
            client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, timeout=timeout, max_retries=0)
        self._client = client
        self.model = model
        self.max_retries = max_retries

    def complete(self, system: str, user: str) -> str:
        import openai

        retryable = (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError)
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=0.2,
                    max_tokens=1500,
                )
                text = _THINK.sub("", resp.choices[0].message.content or "").strip()
                if not text:
                    raise LLMError("LLM returned an empty response")
                return text
            except retryable as exc:
                last = exc
                wait = min(2 ** attempt, 10)
                log.warning("Groq call failed (attempt %d/%d): %s; retrying in %ss", attempt, self.max_retries, exc, wait)
                time.sleep(wait)
            except openai.AuthenticationError as exc:
                raise LLMError("Groq rejected the API key (check GROQ_API_KEY)") from exc
            except openai.APIError as exc:
                raise LLMError(f"Groq API error: {exc}") from exc
        raise LLMError(f"Groq unavailable after {self.max_retries} attempts: {last}")


def build_llm(settings) -> LLM | None:
    if not settings.llm_ready:
        return None
    return GroqLLM(settings.groq_api_key, settings.groq_model)
