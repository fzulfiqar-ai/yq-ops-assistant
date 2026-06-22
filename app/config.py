"""Central configuration. Loads from environment (.env locally, platform vars in prod).

No real secrets live in code — only .env.example has placeholders.
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings:
    """Typed access to environment configuration."""

    def __init__(self) -> None:
        # Supabase
        self.supabase_url: str = os.getenv("SUPABASE_URL", "")
        self.supabase_key: str = os.getenv("SUPABASE_KEY", "")
        self.supabase_jwt_secret: str = os.getenv("SUPABASE_JWT_SECRET", "")

        # Free LLM providers (used by app/llm_router.py)
        self.openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
        self.groq_api_key: str = os.getenv("GROQ_API_KEY", "")
        self.cerebras_api_key: str = os.getenv("CEREBRAS_API_KEY", "")
        self.scaleway_api_key: str = os.getenv("SCALEWAY_API_KEY", "")
        self.together_api_key: str = os.getenv("TOGETHER_API_KEY", "")

        # App
        self.allowed_origins: list[str] = _split_csv(os.getenv("ALLOWED_ORIGINS")) or [
            "http://localhost:8501"
        ]
        self.rate_limit: str = os.getenv("RATE_LIMIT", "30/minute")
        self.dashboard_secret: str = os.getenv("DASHBOARD_SECRET", "yq2024")
        # Machine-to-machine key for schedulers / n8n agent flows (X-Agent-Key header).
        # Empty by default → agent-key auth is disabled until set in the environment.
        self.agent_api_key: str = os.getenv("AGENT_API_KEY", "")

    def require_supabase(self) -> None:
        """Raise a clear error if Supabase config is missing (used by scripts/DB paths)."""
        missing = [
            name
            for name, val in {
                "SUPABASE_URL": self.supabase_url,
                "SUPABASE_KEY": self.supabase_key,
            }.items()
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"Missing required Supabase env vars: {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
