"""Tests for assembler primitives and functions."""
from ollama_sentinel.context.assembler import (
    ContextItem,
    Priority,
    Section,
    chunk_by_lines,
)
from ollama_sentinel.context.tokens import TokenCounter


class TestChunkByLines:
    def test_short_content_returns_single_chunk(self):
        counter = TokenCounter()
        text = "line one\nline two\n"
        assert chunk_by_lines(text, counter=counter, max_tokens=100, overlap_tokens=0) == [text]

    def test_splits_at_line_boundaries(self):
        counter = TokenCounter()
        text = "\n".join(f"line {i}" for i in range(50))
        chunks = chunk_by_lines(text, counter=counter, max_tokens=20, overlap_tokens=0)
        assert len(chunks) >= 2
        # Every chunk ends on a complete line (no partial line at the boundary).
        for ch in chunks[:-1]:
            assert ch.endswith("\n") or ch == chunks[-1]

    def test_overlap_includes_trailing_lines_of_previous_chunk(self):
        counter = TokenCounter()
        text = "\n".join(f"line {i}" for i in range(50))
        chunks = chunk_by_lines(text, counter=counter, max_tokens=20, overlap_tokens=10)
        assert len(chunks) >= 2
        # Last lines of chunk[0] should appear at the start of chunk[1].
        tail = chunks[0].splitlines()[-1]
        assert tail in chunks[1]


class TestDataclasses:
    def test_section_is_frozen(self):
        s = Section(
            name="FILE",
            items=["hello"],
            priority=Priority.MUST_FIT,
            soft_budget=100,
        )
        try:
            s.name = "mutated"
        except Exception as e:
            assert (
                "frozen" in str(e).lower()
                or "immutable" in str(e).lower()
                or "frozen" in type(e).__name__.lower()
            )
        else:  # pragma: no cover
            raise AssertionError("Section should be frozen")

    def test_context_item_has_text_and_embed_key(self):
        item = ContextItem(text="hello", embed_key="k1")
        assert item.text == "hello"
        assert item.embed_key == "k1"
