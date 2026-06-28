"""Local text embeddings — fastembed (CPU/ONNX). No network, no PII egress, so it's safe
to embed text containing customer names. Model: BAAI/bge-small-en-v1.5 (384-dim), lazy-loaded
once on first use (first call downloads ~130MB, then cached on disk).
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding
    log.info("loading local embedding model %s", settings.embed_model)
    return TextEmbedding(settings.embed_model)


def embed(text: str) -> list[float]:
    """Embed one string -> 384-dim vector (list of floats)."""
    return [float(x) for x in next(_model().embed([text or ""]))]


def to_pgvector(vec: list[float]) -> str:
    """Render a vector as a pgvector literal '[..]' for SQL/RPC."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def available() -> bool:
    try:
        _model()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("embeddings unavailable: %s", e)
        return False
