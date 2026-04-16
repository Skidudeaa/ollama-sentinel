"""Retrievers that rank context items.

NullRetriever is the identity fallback. SemanticRetriever (added in a later
task) cosine-ranks items against an Ollama-computed query embedding.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from typing import List, Sequence

from ollama_sentinel.context.assembler import ContextItem
from ollama_sentinel.context.embeddings import EmbeddingUnavailable, OllamaEmbedder

log = logging.getLogger("ollama-sentinel")


class NullRetriever:
    """Returns items in their original order."""

    async def rank(
        self, items: Sequence[ContextItem], _query: str
    ) -> List[ContextItem]:
        return list(items)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class SemanticRetriever:
    """Ranks items by cosine similarity of their embeddings against the query."""

    def __init__(self, embedder: OllamaEmbedder):
        self._embedder = embedder

    async def rank(
        self, items: Sequence[ContextItem], query: str
    ) -> List[ContextItem]:
        items = list(items)
        if not items:
            return items
        query_key = f"query:{hashlib.sha256(query.encode('utf-8')).hexdigest()}"

        try:
            query_vec = await self._embedder.embed(query, cache_key=query_key)
            item_vecs = await asyncio.gather(
                *(self._embedder.embed(i.text, cache_key=i.embed_key) for i in items)
            )
        except EmbeddingUnavailable as e:
            log.warning("Semantic embedding unavailable (%s); using identity order.", e)
            return items

        scored = sorted(
            zip(items, item_vecs),
            key=lambda pair: _cosine(query_vec, pair[1]),
            reverse=True,
        )
        return [item for item, _vec in scored]
