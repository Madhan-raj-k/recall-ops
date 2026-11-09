"""Runtime configuration, read from environment variables (optionally a local .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:  # python-dotenv is optional at runtime
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

DEFAULT_HINDSIGHT_BASE_URL = "https://api.hindsight.vectorize.io"
DEFAULT_BANK_ID = "recallops-shopnova-oncall"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


@dataclass(frozen=True)
class Settings:
    hindsight_api_key: str | None
    hindsight_base_url: str
    bank_id: str
    groq_api_key: str | None
    groq_model: str

    @property
    def hindsight_ready(self) -> bool:
        # A self-hosted server may run without auth, so a non-cloud URL is enough.
        return bool(self.hindsight_api_key) or self.hindsight_base_url != DEFAULT_HINDSIGHT_BASE_URL

    @property
    def llm_ready(self) -> bool:
        return bool(self.groq_api_key)


def load_settings() -> Settings:
    return Settings(
        hindsight_api_key=os.getenv("HINDSIGHT_API_KEY") or None,
        hindsight_base_url=(os.getenv("HINDSIGHT_BASE_URL") or DEFAULT_HINDSIGHT_BASE_URL).rstrip("/"),
        bank_id=os.getenv("HINDSIGHT_BANK_ID") or DEFAULT_BANK_ID,
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        groq_model=os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL,
    )
