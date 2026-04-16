"""Tests for retrievers."""
import pytest

from ollama_sentinel.context.assembler import ContextItem
from ollama_sentinel.context.retrievers import NullRetriever


class TestNullRetriever:
    async def test_returns_items_unchanged(self):
        items = [ContextItem(text=f"i{n}", embed_key=f"k{n}") for n in range(3)]
        out = await NullRetriever().rank(items, query="anything")
        assert out == items
