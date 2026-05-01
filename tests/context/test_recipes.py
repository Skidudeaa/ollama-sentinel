"""Integration tests for the two recipes."""
import hashlib

from ollama_sentinel.context.recipes import build_review_context
from ollama_sentinel.context.retrievers import NullRetriever, SemanticRetriever
from ollama_sentinel.context.tokens import TokenCounter


class _FakeEmbedder:
    """Returns pre-mapped vectors from a dict keyed by cache_key or text."""
    def __init__(self, vectors: dict):
        self._vectors = vectors

    async def embed(self, text, *, cache_key=None):
        key = cache_key if cache_key in self._vectors else text
        return self._vectors[key]


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

    async def test_semantic_retriever_ranks_violations_by_similarity(self):
        content = "x = 1\n"
        query_key = f"query:{hashlib.sha256(content.encode()).hexdigest()}"
        # finding:1 → cosine 1.0 (high similarity); finding:2 → cosine 0.0
        embedder = _FakeEmbedder({
            query_key: [1.0, 0.0],
            "finding:1": [1.0, 0.0],
            "finding:2": [0.0, 1.0],
        })
        retriever = SemanticRetriever(embedder=embedder)
        # violations listed in reverse id order so NullRetriever would put id:2 first
        violations = [
            {
                "id": 2, "severity": "medium", "category": "perf",
                "line_start": 20, "description": "O(n^2) loop",
                "file_path": "src/a.py", "occurrence_count": 1,
                "first_seen": "2026-01-01T00:00:00",
            },
            {
                "id": 1, "severity": "high", "category": "security",
                "line_start": 10, "description": "hardcoded password",
                "file_path": "src/a.py", "occurrence_count": 3,
                "first_seen": "2026-01-01T00:00:00",
            },
        ]
        counter = TokenCounter()
        out = await build_review_context(
            file_rel_path="src/a.py",
            file_type="py",
            content=content,
            diff=None,
            chunk_info="",
            prior_violations=violations,
            counter=counter,
            total_budget=1000,
            retriever=retriever,
        )
        assert "PRIOR UNRESOLVED" in out
        pos_1 = out.index("hardcoded password")
        pos_2 = out.index("O(n^2)")
        assert pos_1 < pos_2


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


from ollama_sentinel.context.recipes import build_triage_context
from ollama_sentinel.triage.extractor import Reference


class TestBuildTriageContext:
    async def test_tool_output_present(self, tmp_path):
        counter = TokenCounter()
        out = await build_triage_context(
            tool_output="Traceback: ValueError: x is bad",
            references=[],
            explicit_context_files=[],
            counter=counter,
            total_budget=500,
            cwd=tmp_path,
        )
        assert "TOOL OUTPUT:" in out
        assert "ValueError: x is bad" in out

    async def test_referenced_source_rendered_in_frequency_order(self, tmp_path):
        # foo.py mentioned twice, bar.py once — foo appears first.
        (tmp_path / "foo.py").write_text("\n".join(f"line {i}" for i in range(1, 30)))
        (tmp_path / "bar.py").write_text("\n".join(f"line {i}" for i in range(1, 30)))
        refs = [
            Reference(path="foo.py", line=5, tool_hint="traceback"),
            Reference(path="foo.py", line=10, tool_hint="traceback"),
            Reference(path="bar.py", line=3, tool_hint="traceback"),
        ]
        counter = TokenCounter()
        out = await build_triage_context(
            tool_output="error",
            references=refs,
            explicit_context_files=[],
            counter=counter,
            total_budget=2000,
            cwd=tmp_path,
        )
        assert "REFERENCED SOURCE:" in out
        assert out.index("foo.py") < out.index("bar.py")

    async def test_window_clamps_at_file_start(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("\n".join(f"line {i}" for i in range(1, 30)))
        refs = [Reference(path="x.py", line=3, tool_hint="traceback")]
        counter = TokenCounter()
        out = await build_triage_context(
            tool_output="error",
            references=refs,
            explicit_context_files=[],
            counter=counter,
            total_budget=2000,
            cwd=tmp_path,
        )
        # Window for line 3 is max(1, 3-8)=1 to min(29, 3+8)=11 — 11 lines.
        # File has 29 lines, 11/29 ≈ 0.38 — windowed with prefixes.
        assert "0001|" in out or "line 1" in out
        assert "0011|" in out or "line 11" in out
        assert "0012" not in out

    async def test_whole_file_when_window_covers_most(self, tmp_path):
        f = tmp_path / "s.py"
        f.write_text("line 1\nline 2\nline 3\n")  # 3 lines
        refs = [Reference(path="s.py", line=2, tool_hint="traceback")]
        counter = TokenCounter()
        out = await build_triage_context(
            tool_output="error",
            references=refs,
            explicit_context_files=[],
            counter=counter,
            total_budget=2000,
            cwd=tmp_path,
        )
        # Window 1..3 covers 100% of a 3-line file — whole file, no prefixes.
        assert "line 1" in out and "line 2" in out and "line 3" in out
        assert "0001|" not in out

    async def test_user_provided_after_auto_extracted(self, tmp_path):
        (tmp_path / "auto.py").write_text("auto body\n")
        (tmp_path / "user.py").write_text("user body\n")
        refs = [Reference(path="auto.py", line=1, tool_hint="traceback")]
        counter = TokenCounter()
        out = await build_triage_context(
            tool_output="error",
            references=refs,
            explicit_context_files=[tmp_path / "user.py"],
            counter=counter,
            total_budget=2000,
            cwd=tmp_path,
        )
        assert "REFERENCED SOURCE:" in out and "USER-PROVIDED CONTEXT:" in out
        assert out.index("REFERENCED SOURCE:") < out.index("USER-PROVIDED CONTEXT:")
