"""Tests for OllamaEmbedder."""
import httpx
import pytest
from pytest_httpx import HTTPXMock

from ollama_sentinel.context.embeddings import EmbeddingUnavailable, OllamaEmbedder


EMBED_URL = "http://localhost:11434/api/embeddings"


class _FakeCache:
    def __init__(self):
        self.store: dict = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl=None):
        self.store[key] = value
        return True


class TestOllamaEmbedder:
    async def test_cache_miss_populates_cache(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=EMBED_URL,
            json={"embedding": [0.1, 0.2, 0.3]},
        )
        cache = _FakeCache()
        emb = OllamaEmbedder(host="http://localhost:11434", cache=cache)
        try:
            vec = await emb.embed("hello", cache_key="k1")
        finally:
            await emb.close()
        assert vec == [0.1, 0.2, 0.3]
        assert cache.store["embed:qwen3-embedding:4b:k1"] == [0.1, 0.2, 0.3]

    async def test_cache_hit_skips_http(self, httpx_mock: HTTPXMock):
        cache = _FakeCache()
        cache.store["embed:qwen3-embedding:4b:k1"] = [0.9, 0.9, 0.9]
        emb = OllamaEmbedder(host="http://localhost:11434", cache=cache)
        try:
            vec = await emb.embed("hello", cache_key="k1")
        finally:
            await emb.close()
        assert vec == [0.9, 0.9, 0.9]
        # No request recorded → no mock consumed.
        assert len(httpx_mock.get_requests()) == 0

    async def test_model_name_is_part_of_key(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=EMBED_URL, json={"embedding": [1.0]})
        cache = _FakeCache()
        # Pre-seed a value for a different model — must not be returned.
        cache.store["embed:other-model:k1"] = [0.0]
        emb = OllamaEmbedder(host="http://localhost:11434", model="qwen3-embedding:4b", cache=cache)
        try:
            vec = await emb.embed("hello", cache_key="k1")
        finally:
            await emb.close()
        assert vec == [1.0]

    async def test_timeout_raises_embedding_unavailable(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
        emb = OllamaEmbedder(host="http://localhost:11434", cache=_FakeCache())
        try:
            with pytest.raises(EmbeddingUnavailable):
                await emb.embed("hello", cache_key="k1")
        finally:
            await emb.close()

    async def test_404_raises_embedding_unavailable(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=EMBED_URL, status_code=404, json={"error": "model not found"})
        emb = OllamaEmbedder(host="http://localhost:11434", cache=_FakeCache())
        try:
            with pytest.raises(EmbeddingUnavailable):
                await emb.embed("hello", cache_key="k1")
        finally:
            await emb.close()
