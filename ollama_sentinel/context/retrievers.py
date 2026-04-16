"""Retrievers that rank context items.

NullRetriever is the identity fallback. SemanticRetriever (added in a later
task) cosine-ranks items against an Ollama-computed query embedding.
"""
from __future__ import annotations

from typing import List, Sequence

from ollama_sentinel.context.assembler import ContextItem


class NullRetriever:
    """Returns items in their original order."""

    async def rank(
        self, items: Sequence[ContextItem], _query: str
    ) -> List[ContextItem]:
        return list(items)
