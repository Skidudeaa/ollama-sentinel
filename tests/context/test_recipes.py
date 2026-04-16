"""Integration tests for the two recipes."""
from ollama_sentinel.context.recipes import build_review_context
from ollama_sentinel.context.retrievers import NullRetriever
from ollama_sentinel.context.tokens import TokenCounter


class TestBuildReviewContext:
    async def test_file_only(self):
        counter = TokenCounter()
        out = await build_review_context(
            file_rel_path="src/foo.py",
            file_type="py",
            content="def foo():\n    return 42\n",
            diff=None,
            chunk_info="",
            prior_violations=[],
            counter=counter,
            total_budget=500,
            retriever=NullRetriever(),
        )
        assert "FILE: src/foo.py" in out
        assert "```py" in out
        assert "def foo" in out
        assert "PRIOR UNRESOLVED" not in out

    async def test_diff_path_renders_diff_block(self):
        counter = TokenCounter()
        out = await build_review_context(
            file_rel_path="src/foo.py",
            file_type="py",
            content=None,
            diff="@@ -1 +1 @@\n-old\n+new",
            chunk_info="",
            prior_violations=[],
            counter=counter,
            total_budget=500,
            retriever=NullRetriever(),
        )
        assert "```diff" in out
        assert "+new" in out

    async def test_prior_violations_rendered_as_items(self):
        counter = TokenCounter()
        violations = [
            {
                "id": 1, "severity": "high", "category": "security",
                "line_start": 10, "line_end": 10,
                "description": "hardcoded password",
                "file_path": "src/a.py", "occurrence_count": 3,
                "first_seen": "2026-01-01T00:00:00",
            },
            {
                "id": 2, "severity": "medium", "category": "perf",
                "line_start": 20, "line_end": 20,
                "description": "O(n^2) loop",
                "file_path": "src/a.py", "occurrence_count": 1,
                "first_seen": "2026-04-01T00:00:00",
            },
        ]
        out = await build_review_context(
            file_rel_path="src/a.py",
            file_type="py",
            content="x = 1\n",
            diff=None,
            chunk_info=" (Part 1/2)",
            prior_violations=violations,
            counter=counter,
            total_budget=500,
            retriever=NullRetriever(),
        )
        assert "FILE: src/a.py (Part 1/2)" in out
        assert "PRIOR UNRESOLVED ISSUES" in out
        assert "[high]" in out and "hardcoded password" in out
        assert "seen 3x since 2026-01-01" in out


from dataclasses import dataclass, field
from typing import List

from ollama_sentinel.context.recipes import build_research_context


@dataclass
class _FakeContentItem:
    url: str = ""
    title: str = ""
    content: str = ""


@dataclass
class _FakeImpactItem:
    file_path: str = ""
    line_number: int = 0
    pattern: str = ""
    severity: str = "LOW"
    action: str = ""
    entity: str = ""


@dataclass
class _FakeImpactAnalysis:
    query: str = ""
    entity_count: int = 0
    affected_files: List[str] = field(default_factory=list)
    items: List[_FakeImpactItem] = field(default_factory=list)
    timestamp: float = 0.0


class TestBuildResearchContext:
    async def test_code_and_sources(self):
        counter = TokenCounter()
        sources = [
            _FakeContentItem(url="http://a", title="A", content="alpha body"),
            _FakeContentItem(url="http://b", title="B", content="beta body"),
        ]
        out = await build_research_context(
            query="how do I migrate?",
            web_sources=sources,
            code_results="matched lines: ...",
            impact=None,
            counter=counter,
            total_budget=1000,
            retriever=NullRetriever(),
        )
        assert "CODE CONTEXT" in out and "matched lines" in out
        assert "WEB SOURCES" in out and "http://a" in out and "alpha body" in out
        assert "IMPACT ANALYSIS" not in out

    async def test_impact_renders_first(self):
        counter = TokenCounter()
        impact = _FakeImpactAnalysis(
            query="q",
            entity_count=1,
            affected_files=["a.py"],
            items=[_FakeImpactItem(file_path="a.py", line_number=1, pattern="x", severity="HIGH", action="fix it")],
        )
        out = await build_research_context(
            query="q",
            web_sources=[],
            code_results=None,
            impact=impact,
            counter=counter,
            total_budget=1000,
            retriever=NullRetriever(),
        )
        assert "IMPACT ANALYSIS" in out
        assert "a.py:1" in out and "fix it" in out
