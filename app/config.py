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
        # Additional free providers — each auto-joins the rotation only when its key is set.
        self.gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")          # aistudio.google.com
        self.mistral_api_key: str = os.getenv("MISTRAL_API_KEY", "")        # console.mistral.ai
        self.cohere_api_key: str = os.getenv("COHERE_API_KEY", "")          # dashboard.cohere.com
        self.github_models_token: str = os.getenv("GITHUB_MODELS_TOKEN", "")  # github.com/marketplace/models
        self.cloudflare_account_id: str = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
        self.cloudflare_api_token: str = os.getenv("CLOUDFLARE_API_TOKEN", "")
        self.sambanova_api_key: str = os.getenv("SAMBANOVA_API_KEY", "")      # cloud.sambanova.ai (very fast)
        self.nvidia_api_key: str = os.getenv("NVIDIA_API_KEY", "")            # build.nvidia.com (DeepSeek/Llama/Nemotron)
        self.moonshot_api_key: str = os.getenv("MOONSHOT_API_KEY", "")        # platform.moonshot.ai (Kimi K2, long-context)
        # Local embeddings (Phase 1 RAG) — model is CPU/ONNX, no network, no PII egress.
        self.embed_model: str = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
        # Notifications — Telegram (free) for reminders/alerts; absent token => disabled.
        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")    # @BotFather
        self.telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")        # your chat/group id
        # Optional web search (Phase 2 vendor-sourcing tool) — Tavily free tier.
        self.tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")            # tavily.com (free)
        # YouTube Data API v3 — trend_radar video signals (accessory unboxing/review volume).
        self.youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")          # console.cloud.google.com

        # Marketing & outreach (Phase: 10k/month) — all optional/config-gated.
        self.wa_human_number: str = os.getenv("WA_HUMAN_NUMBER", "")      # owner's WhatsApp digits (catalog CTA, assist mode)
        self.optout_secret: str = os.getenv("OPTOUT_SECRET", "")          # falls back to SUPABASE_JWT_SECRET
        # WhatsApp Cloud API (Phase 2 — a SECOND SIM, never the daily number)
        self.wa_phone_number_id: str = os.getenv("WA_PHONE_NUMBER_ID", "")
        self.wa_access_token: str = os.getenv("WA_ACCESS_TOKEN", "")      # permanent System User token
        self.wa_verify_token: str = os.getenv("WA_VERIFY_TOKEN", "")      # webhook handshake
        self.meta_app_secret: str = os.getenv("META_APP_SECRET", "")      # X-Hub-Signature-256 HMAC
        # Social publishing (Meta app in dev mode posts to OWN Page/IG — no review needed)
        self.fb_page_id: str = os.getenv("FB_PAGE_ID", "")
        self.fb_page_token: str = os.getenv("FB_PAGE_TOKEN", "")
        self.ig_business_id: str = os.getenv("IG_BUSINESS_ID", "")
        # Agnes AI (agnes-ai.com) — free OpenAI-compatible AI image/video backend for the
        # content engine. Absent key → falls back to the local FFmpeg renderer.
        self.agnes_api_key: str = os.getenv("AGNES_API_KEY", "")

        # App
        self.allowed_origins: list[str] = _split_csv(os.getenv("ALLOWED_ORIGINS")) or [
            "http://localhost:8501"
        ]
        self.rate_limit: str = os.getenv("RATE_LIMIT", "30/minute")
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
