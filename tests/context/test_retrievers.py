"""Tests for retrievers."""
from ollama_sentinel.context.assembler import ContextItem
from ollama_sentinel.context.retrievers import NullRetriever


class TestNullRetriever:
    async def test_returns_items_unchanged(self):
        items = [ContextItem(text=f"i{n}", embed_key=f"k{n}") for n in range(3)]
        out = await NullRetriever().rank(items, _query="anything")
        assert out == items


from ollama_sentinel.context.embeddings import EmbeddingUnavailable
from ollama_sentinel.context.retrievers import SemanticRetriever


class _FakeEmbedder:
    """Returns pre-mapped vectors from a dict; raises on missing keys if asked."""
    def __init__(self, vectors: dict, *, raise_on_keys: set | None = None):
        self._vectors = vectors
        self._raise = raise_on_keys or set()

    async def embed(self, text, *, cache_key=None):
        if cache_key in self._raise or text in self._raise:
            raise EmbeddingUnavailable("boom")
        key = cache_key if cache_key in self._vectors else text
        return self._vectors[key]


class TestSemanticRetriever:
    async def test_orders_by_cosine_similarity(self):
        # query vector = [1, 0], item-A = [1, 0] (cosine 1.0), item-B = [0, 1] (cosine 0.0).
        embedder = _FakeEmbedder({
            "query_key": [1.0, 0.0],
            "a": [1.0, 0.0],
            "b": [0.0, 1.0],
        })
        retriever = SemanticRetriever(embedder=embedder)
        items = [
            ContextItem(text="B-text", embed_key="b"),
            ContextItem(text="A-text", embed_key="a"),
        ]
        ranked = await retriever.rank(items, query="query_key")
        assert ranked[0].embed_key == "a"
        assert ranked[1].embed_key == "b"

    async def test_falls_back_to_identity_on_embedding_unavailable(self, caplog):
        embedder = _FakeEmbedder(
            {"query_key": [1.0, 0.0], "a": [1.0, 0.0]},
            raise_on_keys={"b"},
        )
        retriever = SemanticRetriever(embedder=embedder)
        items = [
            ContextItem(text="A-text", embed_key="a"),
            ContextItem(text="B-text", embed_key="b"),
        ]
        with caplog.at_level("WARNING"):
            ranked = await retriever.rank(items, query="query_key")
        assert [i.embed_key for i in ranked] == ["a", "b"]  # original order
        assert "embedding" in caplog.text.lower()
