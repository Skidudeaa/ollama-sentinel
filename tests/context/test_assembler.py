"""Tests for assembler primitives and functions."""
import pytest

from ollama_sentinel.context.assembler import (
    ContextItem,
    Priority,
    Section,
    assemble,
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


# ---------------------------------------------------------------------------
# Fake helpers for assemble() tests.
# ---------------------------------------------------------------------------

class _CountByLen:
    """Fake TokenCounter: treats character count as token count."""
    def count(self, text: str) -> int:
        return len(text)

    def truncate_to_budget(self, text: str, *, budget: int, direction: str = "tail") -> str:
        if budget <= 0:
            return ""
        if len(text) <= budget:
            return text
        return text[:budget] if direction == "tail" else text[-budget:]


class _ReverseRetriever:
    async def rank(self, items, query):
        return list(reversed(list(items)))


# ---------------------------------------------------------------------------
# MUST_FIT tests.
# ---------------------------------------------------------------------------

class TestAssembleMustFit:
    async def test_must_fit_always_renders(self):
        counter = _CountByLen()
        sections = [Section("FILE", ["abc"], Priority.MUST_FIT, soft_budget=10)]
        out = await assemble(sections, total_budget=10, counter=counter)
        assert "FILE:" in out and "abc" in out

    async def test_must_fit_sum_over_budget_is_scaled_proportionally(self, caplog):
        counter = _CountByLen()
        sections = [
            Section("A", ["x" * 100], Priority.MUST_FIT, soft_budget=80, truncate="tail"),
            Section("B", ["y" * 100], Priority.MUST_FIT, soft_budget=80, truncate="tail"),
        ]
        with caplog.at_level("WARNING"):
            out = await assemble(sections, total_budget=100, counter=counter)
        # Scale factor = 100/160 = 0.625; each must_budget = int(80 * 0.625) = 50.
        # Header "A:\n" = 3 chars -> body_budget = 47.
        # Suffix "\n… [truncated]" = 14 chars -> text room = max(1, 47-14) = 33.
        # Assert a conservative threshold well within the computed 33 chars.
        assert "x" * 30 in out and "y" * 30 in out
        assert "recipe bug" in caplog.text.lower() or "exceeds" in caplog.text.lower()


# ---------------------------------------------------------------------------
# OPTIONAL tests.
# ---------------------------------------------------------------------------

class TestAssembleOptional:
    async def test_optional_section_dropped_when_no_room(self):
        counter = _CountByLen()
        sections = [
            Section("FILE", ["a" * 100], Priority.MUST_FIT, soft_budget=100),
            Section("HIST", ["b" * 50], Priority.OPTIONAL, soft_budget=50),
        ]
        out = await assemble(sections, total_budget=100, counter=counter)
        assert "FILE:" in out and "HIST" not in out

    async def test_optional_retriever_ranks_items(self):
        counter = _CountByLen()
        items = [
            ContextItem(text="first", embed_key="k1"),
            ContextItem(text="second", embed_key="k2"),
            ContextItem(text="third", embed_key="k3"),
        ]
        sections = [
            Section("HIST", items, Priority.OPTIONAL, soft_budget=30,
                    retriever=_ReverseRetriever()),
        ]
        # total_budget=30 fits header(6) + "third"(6) + "second"(7) + "first"(6) = 25.
        out = await assemble(sections, total_budget=30, counter=counter, query="q")
        # Reverse retriever -> "third" appears before "first" in output.
        assert out.index("third") < out.index("first")

    async def test_empty_optional_section_is_dropped(self):
        counter = _CountByLen()
        sections = [
            Section("FILE", ["a"], Priority.MUST_FIT, soft_budget=5),
            Section("HIST", [], Priority.OPTIONAL, soft_budget=10),
        ]
        out = await assemble(sections, total_budget=50, counter=counter)
        assert "HIST" not in out


# ---------------------------------------------------------------------------
# Truncation tests.
# ---------------------------------------------------------------------------

class TestAssembleTruncation:
    async def test_single_item_overflow_truncated_tail(self):
        counter = _CountByLen()
        sections = [
            Section("FILE", ["x" * 200], Priority.MUST_FIT, soft_budget=50, truncate="tail"),
        ]
        out = await assemble(sections, total_budget=50, counter=counter)
        # Header "FILE:\n" = 6 chars -> body_budget = 44.
        # Suffix "\n… [truncated]" = 14 chars -> text room = max(1, 44-14) = 30.
        # Assert a conservative threshold well within the computed 30 chars.
        assert "FILE:" in out or out.startswith("FILE:\n")
        assert "x" * 28 in out
        assert "truncated" in out

    async def test_single_item_overflow_truncated_head(self):
        counter = _CountByLen()
        content = "START" + ("x" * 200) + "END"
        sections = [
            Section("FILE", [content], Priority.MUST_FIT, soft_budget=50, truncate="head"),
        ]
        out = await assemble(sections, total_budget=50, counter=counter)
        # Keeps tail of content (direction="head" means truncate from head, keep tail).
        assert "END" in out
        assert "truncated" in out
