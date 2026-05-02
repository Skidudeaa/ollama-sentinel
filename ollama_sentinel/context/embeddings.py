"""Ollama embedding client.

Single responsibility: POST to /api/embeddings, cache the vectors, and
raise EmbeddingUnavailable on any failure so callers can degrade gracefully.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Protocol

import httpx

log = logging.getLogger("ollama-sentinel")


class EmbeddingUnavailable(RuntimeError):
    """Raised when the embedding backend cannot serve a request."""


class _CacheLike(Protocol):
    def get(self, key: str): ...
    def set(self, key: str, value, ttl: Optional[int] = None): ...


# Stored forever: 10 years in seconds. diskcache TTL-based caches treat this
# as "effectively permanent." Vectors are invalidated by model-name key change.
_EMBED_TTL_SECONDS = 60 * 60 * 24 * 365 * 10


class OllamaEmbedder:
    def __init__(
        self,
        host: str,
        model: str = "qwen3-embedding:4b",
        cache: Optional[_CacheLike] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout_seconds: float = 30.0,
    ):
        self._host = host.rstrip("/")
        self._model = model
        self._cache = cache
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    @property
    def model(self) -> str:
        return self._model

    def _cache_key(self, cache_key: str) -> str:
        return f"embed:{self._model}:{cache_key}"

    async def embed(self, text: str, *, cache_key: Optional[str] = None) -> List[float]:
        """Return the embedding vector for text. Raise EmbeddingUnavailable on failure."""
        if cache_key and self._cache is not None:
            cached = self._cache.get(self._cache_key(cache_key))
            if isinstance(cached, list) and cached:
                return cached

        url = f"{self._host}/api/embeddings"
        payload = {"model": self._model, "prompt": text}
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, httpx.TimeoutException, ValueError) as e:
            raise EmbeddingUnavailable(f"embedding request failed: {e}") from e

        vec = data.get("embedding")
        if not isinstance(vec, list) or not vec:
            raise EmbeddingUnavailable(f"malformed embedding response: {data!r}")

        if cache_key and self._cache is not None:
            self._cache.set(self._cache_key(cache_key), vec, ttl=_EMBED_TTL_SECONDS)
        return vec

    async def close(self) -> None:
        await self._client.aclose()
