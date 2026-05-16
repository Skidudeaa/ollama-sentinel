# ContextBuilder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** SHIPPED 2026-04-16 — all phases landed. Follow-ups CB-1..CB-7 all closed (CB-1 by commit 1313681; followups.md previously mis-tracked it OPEN). Audit: docs/superpowers/plans/2026-05-15-implementation-audit.md

**Goal:** Introduce a shared, token-budgeted, embedding-ranked context assembler (`ollama_sentinel/context/`) that replaces ad-hoc prompt assembly in the sentinel and the research agent, and upgrade `ViolationDB` to semantic recall via Ollama embeddings.

**Architecture:** Six new files under `ollama_sentinel/context/` (pure assembler + tokenizer wrapper + Ollama embedder + retrievers + two named recipes). `ViolationDB` gains an `embed_text` column and a similarity-ranked query; `EnhancedMemoryStore` gets an optional async upgrade. Failures in the embedding path degrade to the existing exact-match / token-overlap behavior — review output is never blocked by infrastructure.

**Tech Stack:** Python ≥ 3.10, async-first; new deps: `tiktoken` (embedded in core); existing `diskcache` promoted from `[research]` to core; pytest with `asyncio_mode = "auto"` and `pytest-httpx` for all Ollama mocking.

**Source spec:** `docs/superpowers/specs/2026-04-16-context-builder-design.md` (read before implementing).

**Execution guidance:** Commit at the end of every task listed below. Each phase ends with a "run the full suite" gate; do not start the next phase if the suite is red. The full test suite must stay under ~3 seconds — if a new test makes it slower, it's mocking the wrong thing.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `ollama_sentinel/context/__init__.py` | create | Public re-exports: `assemble`, `Section`, `Priority`, `ContextItem`, `Retriever`, `NullRetriever`, `SemanticRetriever`, `OllamaEmbedder`, `TokenCounter`, `EmbeddingUnavailable`, `build_review_context`, `build_research_context`. |
| `ollama_sentinel/context/tokens.py` | create | `TokenCounter` — `tiktoken` happy path + `len(text) // 3.5` fallback estimator. |
| `ollama_sentinel/context/assembler.py` | create | `Priority`, `ContextItem`, `Section`, `Retriever` Protocol, `assemble()`. Pure, no I/O. Also hosts a token-aware `chunk_by_lines()` helper used by the sentinel. |
| `ollama_sentinel/context/embeddings.py` | create | `OllamaEmbedder` + `EmbeddingUnavailable`. Async `httpx` POSTs to `/api/embeddings`, cache-backed. |
| `ollama_sentinel/context/retrievers.py` | create | `NullRetriever`, `SemanticRetriever` (cosine similarity, pure Python). |
| `ollama_sentinel/context/recipes.py` | create | `build_review_context`, `build_research_context`, private `_render_file_block`, `_render_violation`, `_content_item_to_context_item` helpers. |
| `ollama_sentinel/models.py` | modify | Add `EmbeddingConfig`, extend `OllamaModelConfig` (`context_window`, `output_reserve_tokens`), extend `MemoryConfig` (`neighbor_k`, `semantic_recall`), deprecate-in-place `ProcessingConfig.max_chars_per_chunk` and `overlap_chars`, add `embedding: EmbeddingConfig` to `SentinelConfig`. |
| `ollama_sentinel/violation_db.py` | modify | Add `embed_text` column + idempotent `_migrate()`, populate on insert, `get_all_unresolved()`, async `get_neighbors_by_similarity()`. |
| `ollama_sentinel/processor.py` | modify | `format_prompt` becomes async and calls `build_review_context`; chunking switches to token-based via `chunk_by_lines`; `_get_prior_violations` becomes `_get_ranked_prior_violations`. |
| `ollama_sentinel/utils.py` | modify | Delete `chunk_content_by_lines` (moved to `assembler.py`). Keep re-export for back-compat in tests: `from ollama_sentinel.context.assembler import chunk_by_lines as chunk_content_by_lines`. |
| `research_agent/tools/synthesis.py` | modify | `synthesize()` calls `build_research_context`; template replaces `{{#each web_sources}}` with `{{assembled_context}}`. |
| `research_agent/tools/memory.py` | modify (optional phase 8) | Add async `find_similar_queries_semantic` / `find_similar_webpages_semantic` using `SemanticRetriever`; keep sync methods unchanged. |
| `research_agent/core/workflow.py` | modify (optional phase 8) | Wire semantic methods in `analyze` node using the same `new_event_loop` pattern as `read`. |
| `research_agent/core/config.py` | modify | Add `api.synthesis_context_tokens` default (12000). |
| `pyproject.toml` | modify | Add `tiktoken>=0.7.0`; move `diskcache>=5.6.0` from `[research]` to core `dependencies`. |
| `tests/context/__init__.py` | create | Empty — marks test package. |
| `tests/context/test_tokens.py` | create | `TokenCounter` happy path + fallback. |
| `tests/context/test_assembler.py` | create | `assemble()` behavior: must-fit always renders, optional drops when over budget, retriever ordering, proportional must-fit scaling, truncate direction, empty-optional-dropped, `chunk_by_lines`. |
| `tests/context/test_embeddings.py` | create | `OllamaEmbedder` with `pytest-httpx`: cache hit/miss, model-in-key, `EmbeddingUnavailable` on timeout/404. |
| `tests/context/test_retrievers.py` | create | `NullRetriever` identity; `SemanticRetriever` cosine ordering with fake embedder; graceful degradation. |
| `tests/context/test_recipes.py` | create | `build_review_context` and `build_research_context` full integration with fake embedder. |
| `tests/test_violation_db.py` | modify | Add migration idempotency, `get_neighbors_by_similarity`, `embed_text` population tests. |
| `tests/test_processor.py` | modify | Update assertions for async `format_prompt` and recipe-based pipeline. |

---

## Phase 0 — Dependencies & scaffolding

### Task 1: Add `tiktoken` and promote `diskcache`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Edit pyproject.toml**

Change the `dependencies` list to include `tiktoken` and `diskcache`:

```toml
dependencies = [
    "httpx>=0.27.0",
    "watchfiles>=0.22.0",
    "pydantic>=2.5.2",
    "pyyaml>=6.0.1",
    "rich>=13.7.1",
    "typer>=0.12.0",
    "gitpython>=3.1.40",
    "tenacity>=8.2.3",
    "pathspec>=0.11.1",
    "tiktoken>=0.7.0",
    "diskcache>=5.6.0",
]
```

Remove `"diskcache>=5.6.0"` from the `[research]` section (it's now inherited from core).

- [ ] **Step 2: Reinstall and verify imports**

Run:
```bash
pip install -e ".[dev]"
python -c "import tiktoken; import diskcache; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Run the existing suite to catch anything broken**

Run: `pytest tests/ -x -q`
Expected: all 247 tests pass.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add tiktoken, promote diskcache to core"
```

---

### Task 2: Scaffold the `context` package

**Files:**
- Create: `ollama_sentinel/context/__init__.py`
- Create: `tests/context/__init__.py`

- [ ] **Step 1: Create empty test package**

Write `tests/context/__init__.py`:
```python
```
(Literally empty file.)

- [ ] **Step 2: Create context package init**

Write `ollama_sentinel/context/__init__.py`:
```python
"""Shared context-assembly primitives for ollama-sentinel and research_agent.

See docs/superpowers/specs/2026-04-16-context-builder-design.md for the
approved design this implements.
"""
# Public re-exports are populated as modules land in subsequent tasks.
# Keep this file import-safe at all times.
```

- [ ] **Step 3: Verify importability**

Run: `python -c "import ollama_sentinel.context; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add ollama_sentinel/context/__init__.py tests/context/__init__.py
git commit -m "feat(context): scaffold shared context package"
```

---

## Phase 1 — Pure primitives

### Task 3: `TokenCounter` in `tokens.py`

**Files:**
- Create: `ollama_sentinel/context/tokens.py`
- Test: `tests/context/test_tokens.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/context/test_tokens.py`:
```python
"""Tests for TokenCounter."""
from unittest.mock import patch

from ollama_sentinel.context.tokens import TokenCounter


class TestTokenCounter:
    def test_counts_via_tiktoken(self):
        counter = TokenCounter()
        # "hello world" is 2 tokens in cl100k_base.
        assert counter.count("hello world") == 2

    def test_count_empty_string_is_zero(self):
        assert TokenCounter().count("") == 0

    def test_fallback_estimator_when_tiktoken_unavailable(self):
        # Force the fallback path by simulating import failure.
        with patch("ollama_sentinel.context.tokens._try_load_tiktoken", return_value=None):
            counter = TokenCounter()
            # len("abcdefg") // 3.5 -> 2
            assert counter.count("abcdefg") == 2

    def test_truncate_to_budget_returns_prefix(self):
        counter = TokenCounter()
        text = "the quick brown fox jumps over the lazy dog"
        out = counter.truncate_to_budget(text, budget=3, direction="tail")
        assert counter.count(out) <= 3
        assert text.startswith(out)

    def test_truncate_head_returns_suffix(self):
        counter = TokenCounter()
        text = "the quick brown fox jumps over the lazy dog"
        out = counter.truncate_to_budget(text, budget=3, direction="head")
        assert counter.count(out) <= 3
        assert text.endswith(out)
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/context/test_tokens.py -v`
Expected: ImportError — module does not exist.

- [ ] **Step 3: Implement `tokens.py`**

Write `ollama_sentinel/context/tokens.py`:
```python
"""Token counting + budget-aware truncation.

Uses tiktoken (cl100k_base) as a universal approximator across Ollama models.
Falls back to a char-based estimator if tiktoken cannot load.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

log = logging.getLogger("ollama-sentinel")

_FALLBACK_CHARS_PER_TOKEN = 3.5


def _try_load_tiktoken():
    """Return a cl100k_base encoding, or None if tiktoken is unusable."""
    try:
        import tiktoken
        return tiktoken.get_encoding("cl100k_base")
    except Exception as e:  # pragma: no cover — hard to trigger in tests without mocking
        log.warning("tiktoken unavailable (%s); falling back to char-based estimator", e)
        return None


class TokenCounter:
    """Counts tokens and truncates strings to a token budget."""

    def __init__(self):
        self._enc = _try_load_tiktoken()

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is None:
            return int(len(text) / _FALLBACK_CHARS_PER_TOKEN)
        return len(self._enc.encode(text))

    def truncate_to_budget(
        self,
        text: str,
        *,
        budget: int,
        direction: Literal["head", "tail"] = "tail",
    ) -> str:
        """Return the longest prefix/suffix of text that fits within `budget` tokens."""
        if budget <= 0 or not text:
            return ""
        if self._enc is None:
            # Approximate via chars.
            char_budget = int(budget * _FALLBACK_CHARS_PER_TOKEN)
            if len(text) <= char_budget:
                return text
            return text[:char_budget] if direction == "tail" else text[-char_budget:]

        tokens = self._enc.encode(text)
        if len(tokens) <= budget:
            return text
        kept = tokens[:budget] if direction == "tail" else tokens[-budget:]
        return self._enc.decode(kept)
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/context/test_tokens.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/context/tokens.py tests/context/test_tokens.py
git commit -m "feat(context): add TokenCounter with tiktoken + fallback"
```

---

### Task 4: Core dataclasses, `Retriever` Protocol, `EmbeddingUnavailable`

**Files:**
- Create: `ollama_sentinel/context/assembler.py` (partial — just types and `chunk_by_lines`; `assemble()` in next task)

- [ ] **Step 1: Write the failing test for `chunk_by_lines`**

Append to `tests/context/test_assembler.py` (create file):
```python
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
            assert "frozen" in str(e) or "immutable" in str(e)
        else:  # pragma: no cover
            raise AssertionError("Section should be frozen")

    def test_context_item_has_text_and_embed_key(self):
        item = ContextItem(text="hello", embed_key="k1")
        assert item.text == "hello"
        assert item.embed_key == "k1"
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/context/test_assembler.py -v`
Expected: ImportError — assembler.py does not exist.

- [ ] **Step 3: Implement the types**

Write `ollama_sentinel/context/assembler.py`:
```python
"""Token-budgeted section assembler.

Pure module: no I/O, no Ollama calls, no tokenizer instantiation.
All dependencies are injected. `assemble()` is the only entrypoint.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Literal, Optional, Protocol, Sequence, Union

from ollama_sentinel.context.tokens import TokenCounter

log = logging.getLogger("ollama-sentinel")


class Priority(Enum):
    MUST_FIT = "must_fit"
    OPTIONAL = "optional"


@dataclass(frozen=True)
class ContextItem:
    text: str
    embed_key: str


@dataclass(frozen=True)
class Section:
    name: str
    items: Sequence[Union[str, ContextItem]]
    priority: Priority
    soft_budget: int
    retriever: Optional["Retriever"] = None
    truncate: Literal["head", "tail"] = "tail"


class Retriever(Protocol):
    async def rank(
        self, items: Sequence[ContextItem], query: str
    ) -> List[ContextItem]: ...


def chunk_by_lines(
    content: str,
    *,
    counter: TokenCounter,
    max_tokens: int,
    overlap_tokens: int = 0,
) -> List[str]:
    """Split content into chunks that each fit within max_tokens, preferring
    line boundaries. Adjacent chunks share `overlap_tokens` worth of trailing
    lines from the previous chunk.

    Replaces utils.chunk_content_by_lines, which counted chars.
    """
    if counter.count(content) <= max_tokens:
        return [content]

    lines = content.splitlines(keepends=True)
    chunks: List[str] = []
    current: List[str] = []
    current_tokens = 0

    for line in lines:
        line_tokens = counter.count(line)
        if current and current_tokens + line_tokens > max_tokens:
            chunks.append("".join(current))
            # Build overlap from trailing lines of the chunk we just emitted.
            overlap: List[str] = []
            overlap_t = 0
            for prev in reversed(current):
                t = counter.count(prev)
                if overlap_t + t > overlap_tokens:
                    break
                overlap.insert(0, prev)
                overlap_t += t
            current = overlap
            current_tokens = overlap_t
        current.append(line)
        current_tokens += line_tokens

    if current:
        chunks.append("".join(current))
    return chunks
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/context/test_assembler.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/context/assembler.py tests/context/test_assembler.py
git commit -m "feat(context): add core dataclasses and chunk_by_lines"
```

---

### Task 5: Implement `assemble()` + `NullRetriever`

**Files:**
- Modify: `ollama_sentinel/context/assembler.py`
- Create: `ollama_sentinel/context/retrievers.py` (partial — just `NullRetriever`)
- Test: `tests/context/test_assembler.py`, `tests/context/test_retrievers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/context/test_assembler.py`:
```python
import pytest

from ollama_sentinel.context.assembler import assemble


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
        # Each section shrunk to ~50 chars (rough proportion).
        assert "x" * 40 in out and "y" * 40 in out
        assert "recipe bug" in caplog.text.lower() or "exceeds" in caplog.text.lower()


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
        from ollama_sentinel.context.assembler import ContextItem
        items = [
            ContextItem(text="first", embed_key="k1"),
            ContextItem(text="second", embed_key="k2"),
            ContextItem(text="third", embed_key="k3"),
        ]
        sections = [
            Section("HIST", items, Priority.OPTIONAL, soft_budget=20,
                    retriever=_ReverseRetriever()),
        ]
        out = await assemble(sections, total_budget=20, counter=counter, query="q")
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


class TestAssembleTruncation:
    async def test_single_item_overflow_truncated_tail(self):
        counter = _CountByLen()
        sections = [
            Section("FILE", ["x" * 200], Priority.MUST_FIT, soft_budget=50, truncate="tail"),
        ]
        out = await assemble(sections, total_budget=50, counter=counter)
        # Should contain the head of the content, with an ellipsis suffix.
        assert out.startswith("FILE:\n") or "FILE:" in out
        assert "x" * 40 in out
        assert "truncated" in out

    async def test_single_item_overflow_truncated_head(self):
        counter = _CountByLen()
        content = "START" + ("x" * 200) + "END"
        sections = [
            Section("FILE", [content], Priority.MUST_FIT, soft_budget=50, truncate="head"),
        ]
        out = await assemble(sections, total_budget=50, counter=counter)
        # Keeps tail of content.
        assert "END" in out
        assert "truncated" in out
```

Write `tests/context/test_retrievers.py`:
```python
"""Tests for retrievers."""
import pytest

from ollama_sentinel.context.assembler import ContextItem
from ollama_sentinel.context.retrievers import NullRetriever


class TestNullRetriever:
    async def test_returns_items_unchanged(self):
        items = [ContextItem(text=f"i{n}", embed_key=f"k{n}") for n in range(3)]
        out = await NullRetriever().rank(items, query="anything")
        assert out == items
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/context/ -v`
Expected: ImportError for `assemble` and `NullRetriever`.

- [ ] **Step 3: Implement `assemble()`**

Append to `ollama_sentinel/context/assembler.py`:
```python
_TRUNCATED_SUFFIX = "\n… [truncated]"


async def assemble(
    sections: Sequence[Section],
    *,
    total_budget: int,
    counter: TokenCounter,
    query: Optional[str] = None,
) -> str:
    """Assemble sections into a single prompt-ready string under total_budget.

    See the design spec for the algorithm. Callers inject `counter` and, via
    each Section, optional `retriever`. This function never raises; failures
    in retrievers are logged and the section falls back to identity ordering.
    """
    must = [s for s in sections if s.priority == Priority.MUST_FIT]
    opt = [s for s in sections if s.priority == Priority.OPTIONAL]

    # ------------------------------------------------------------------
    # 1. Reserve budget for MUST_FIT sections. Proportionally scale if
    #    the sum of soft_budgets already exceeds total_budget.
    # ------------------------------------------------------------------
    reserved = sum(s.soft_budget for s in must)
    if reserved > total_budget:
        log.warning(
            "Recipe bug: MUST_FIT sections total %s tokens but total_budget is %s; "
            "scaling proportionally.", reserved, total_budget,
        )
        scale = total_budget / reserved if reserved else 1.0
        must_budgets = {s.name: max(1, int(s.soft_budget * scale)) for s in must}
        reserved = sum(must_budgets.values())
    else:
        must_budgets = {s.name: s.soft_budget for s in must}

    remaining = max(0, total_budget - reserved)

    # ------------------------------------------------------------------
    # 2. Render MUST_FIT sections, truncating single items if needed.
    # ------------------------------------------------------------------
    rendered: List[str] = []
    for s in must:
        body = await _render_section(s, counter=counter, budget=must_budgets[s.name], query=query)
        if body:
            rendered.append(body)

    # ------------------------------------------------------------------
    # 3. Walk OPTIONAL sections in order, filling remaining budget.
    # ------------------------------------------------------------------
    for s in opt:
        if remaining <= 0:
            break
        used, body = await _render_optional_section(
            s, counter=counter, budget=min(s.soft_budget, remaining), query=query,
        )
        if body:
            rendered.append(body)
            remaining -= used

    return "\n\n".join(rendered)


async def _render_section(
    s: Section, *, counter: TokenCounter, budget: int, query: Optional[str]
) -> str:
    """Render a MUST_FIT section. Items are joined; overflow is truncated."""
    if not s.items:
        return ""

    header = f"{s.name}:"
    header_tokens = counter.count(header + "\n")
    body_budget = max(0, budget - header_tokens)
    if body_budget == 0:
        return ""

    joined = "\n".join(_item_text(i) for i in s.items)
    if counter.count(joined) <= body_budget:
        return f"{header}\n{joined}"

    truncated = counter.truncate_to_budget(
        joined, budget=max(1, body_budget - counter.count(_TRUNCATED_SUFFIX)), direction=s.truncate,
    )
    return f"{header}\n{truncated}{_TRUNCATED_SUFFIX}"


async def _render_optional_section(
    s: Section, *, counter: TokenCounter, budget: int, query: Optional[str]
) -> tuple[int, str]:
    """Render an OPTIONAL section. Drop items tail-first until the body fits.

    Returns (tokens_used, rendered_string). If zero items fit, returns (0, "").
    """
    if not s.items:
        return 0, ""

    # Coerce to ContextItem list for consistent handling.
    raw = list(s.items)
    items: List[ContextItem] = [
        i if isinstance(i, ContextItem) else ContextItem(text=str(i), embed_key=f"{s.name}:{idx}")
        for idx, i in enumerate(raw)
    ]

    # Apply retriever if we have one and a query.
    if s.retriever is not None and query is not None:
        try:
            items = await s.retriever.rank(items, query)
        except Exception as e:
            log.warning("Retriever failed for section %s (%s); using original order.", s.name, e)

    header = f"{s.name}:"
    header_tokens = counter.count(header + "\n")
    if header_tokens >= budget:
        return 0, ""

    kept_lines: List[str] = []
    used = header_tokens
    for item in items:
        t = counter.count(item.text) + 1  # +1 for newline join
        if used + t > budget:
            # Try truncating this single item to what's left.
            room = budget - used - 1
            if room <= 0:
                break
            truncated = counter.truncate_to_budget(
                item.text, budget=max(1, room - counter.count(_TRUNCATED_SUFFIX)), direction=s.truncate,
            )
            if truncated:
                kept_lines.append(truncated + _TRUNCATED_SUFFIX)
                used = budget  # exhausted
            break
        kept_lines.append(item.text)
        used += t

    if not kept_lines:
        return 0, ""

    body = "\n".join(kept_lines)
    return used, f"{header}\n{body}"


def _item_text(item: Union[str, ContextItem]) -> str:
    return item.text if isinstance(item, ContextItem) else item
```

- [ ] **Step 4: Implement `NullRetriever`**

Write `ollama_sentinel/context/retrievers.py`:
```python
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
        self, items: Sequence[ContextItem], query: str
    ) -> List[ContextItem]:
        return list(items)
```

- [ ] **Step 5: Run — expect pass**

Run: `pytest tests/context/ -v`
Expected: all tests pass.

- [ ] **Step 6: Re-export in package init**

Replace `ollama_sentinel/context/__init__.py` with:
```python
"""Shared context-assembly primitives for ollama-sentinel and research_agent."""
from ollama_sentinel.context.assembler import (
    ContextItem,
    Priority,
    Retriever,
    Section,
    assemble,
    chunk_by_lines,
)
from ollama_sentinel.context.retrievers import NullRetriever
from ollama_sentinel.context.tokens import TokenCounter

__all__ = [
    "ContextItem",
    "NullRetriever",
    "Priority",
    "Retriever",
    "Section",
    "TokenCounter",
    "assemble",
    "chunk_by_lines",
]
```

- [ ] **Step 7: Verify re-exports**

Run: `python -c "from ollama_sentinel.context import assemble, Section, Priority, NullRetriever; print('ok')"`
Expected: `ok`

- [ ] **Step 8: Commit**

```bash
git add ollama_sentinel/context/ tests/context/
git commit -m "feat(context): add assemble() and NullRetriever"
```

---

## Phase 2 — Ollama embeddings & SemanticRetriever

### Task 6: `OllamaEmbedder` and `EmbeddingUnavailable`

**Files:**
- Create: `ollama_sentinel/context/embeddings.py`
- Test: `tests/context/test_embeddings.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/context/test_embeddings.py`:
```python
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
        assert cache.store["embed:nomic-embed-text:k1"] == [0.1, 0.2, 0.3]

    async def test_cache_hit_skips_http(self, httpx_mock: HTTPXMock):
        cache = _FakeCache()
        cache.store["embed:nomic-embed-text:k1"] = [0.9, 0.9, 0.9]
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
        emb = OllamaEmbedder(host="http://localhost:11434", model="nomic-embed-text", cache=cache)
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
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/context/test_embeddings.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `embeddings.py`**

Write `ollama_sentinel/context/embeddings.py`:
```python
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
        model: str = "nomic-embed-text",
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
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/context/test_embeddings.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/context/embeddings.py tests/context/test_embeddings.py
git commit -m "feat(context): add OllamaEmbedder with cache and error handling"
```

---

### Task 7: `SemanticRetriever`

**Files:**
- Modify: `ollama_sentinel/context/retrievers.py`
- Test: `tests/context/test_retrievers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/context/test_retrievers.py`:
```python
from ollama_sentinel.context.embeddings import EmbeddingUnavailable
from ollama_sentinel.context.retrievers import SemanticRetriever


class _FakeEmbedder:
    """Returns pre-mapped vectors from a dict; raises on missing keys if asked."""
    def __init__(self, vectors: dict, *, raise_on_keys: set = None):
        self._vectors = vectors
        self._raise = raise_on_keys or set()

    async def embed(self, text, *, cache_key=None):
        if cache_key in self._raise or text in self._raise:
            raise EmbeddingUnavailable("boom")
        key = cache_key if cache_key in self._vectors else text
        return self._vectors[key]


class TestSemanticRetriever:
    async def test_orders_by_cosine_similarity(self):
        # query vector ≈ [1, 0], item-A ≈ [1, 0] (sim=1), item-B ≈ [0, 1] (sim=0).
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
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/context/test_retrievers.py -v`
Expected: ImportError for `SemanticRetriever`.

- [ ] **Step 3: Implement `SemanticRetriever`**

Append to `ollama_sentinel/context/retrievers.py`:
```python
import asyncio
import hashlib
import logging
import math
from typing import List, Sequence

from ollama_sentinel.context.embeddings import EmbeddingUnavailable, OllamaEmbedder

log = logging.getLogger("ollama-sentinel")


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
            log.warning("Semantic ranking unavailable (%s); using identity order.", e)
            return items

        scored = sorted(
            zip(items, item_vecs),
            key=lambda pair: _cosine(query_vec, pair[1]),
            reverse=True,
        )
        return [item for item, _vec in scored]
```

Note: import `ContextItem` at top of file if not already present. Update imports:
```python
from ollama_sentinel.context.assembler import ContextItem
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/context/test_retrievers.py -v`
Expected: all tests pass.

- [ ] **Step 5: Update package init**

Modify `ollama_sentinel/context/__init__.py` to add:
```python
from ollama_sentinel.context.embeddings import EmbeddingUnavailable, OllamaEmbedder
from ollama_sentinel.context.retrievers import SemanticRetriever
```
Append `"EmbeddingUnavailable"`, `"OllamaEmbedder"`, `"SemanticRetriever"` to `__all__`.

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/context/ tests/context/test_retrievers.py
git commit -m "feat(context): add SemanticRetriever with cosine ranking"
```

---

## Phase 3 — Recipes

### Task 8: `build_review_context`

**Files:**
- Create: `ollama_sentinel/context/recipes.py` (partial — review recipe only)
- Test: `tests/context/test_recipes.py`

- [ ] **Step 1: Write the failing test**

Write `tests/context/test_recipes.py`:
```python
"""Integration tests for the two recipes."""
from ollama_sentinel.context.assembler import Priority
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
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/context/test_recipes.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `build_review_context`**

Write `ollama_sentinel/context/recipes.py`:
```python
"""Named recipes for the two consumers of the context assembler.

Each recipe encodes the section list, budget ratios, and retriever wiring
for its module. Consumers call one function; they do not hand-assemble.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from ollama_sentinel.context.assembler import (
    ContextItem,
    Priority,
    Retriever,
    Section,
    assemble,
)
from ollama_sentinel.context.tokens import TokenCounter


def _render_file_block(
    content: Optional[str], diff: Optional[str], file_type: str
) -> str:
    if diff is not None:
        return f"```diff\n{diff}\n```"
    body = content if content is not None and content != "" else "<Empty File>"
    return f"```{file_type}\n{body}\n```"


def _render_violation(v: dict) -> str:
    count = v.get("occurrence_count", 1)
    first = (v.get("first_seen") or "unknown")[:10]
    severity = v.get("severity", "medium")
    category = v.get("category", "unknown")
    line = v.get("line_start", 0)
    desc = v.get("description", "")
    return f"- [{severity}] {category} at line {line}: {desc} (seen {count}x since {first})"


async def build_review_context(
    *,
    file_rel_path: str,
    file_type: str,
    content: Optional[str],
    diff: Optional[str],
    chunk_info: str,
    prior_violations: Sequence[dict],
    counter: TokenCounter,
    total_budget: int,
    retriever: Retriever,
) -> str:
    """Sentinel recipe — replaces the body of FileProcessor.format_prompt."""
    sections: List[Section] = [
        Section(
            name=f"FILE: {file_rel_path}{chunk_info}",
            items=[_render_file_block(content, diff, file_type)],
            priority=Priority.MUST_FIT,
            soft_budget=int(total_budget * 0.70),
            truncate="tail",
        ),
    ]
    if prior_violations:
        violation_items = [
            ContextItem(
                text=_render_violation(v),
                embed_key=f"finding:{v.get('id', _hash_violation(v))}",
            )
            for v in prior_violations
        ]
        sections.append(Section(
            name="PRIOR UNRESOLVED ISSUES (address or escalate if still present)",
            items=violation_items,
            priority=Priority.OPTIONAL,
            soft_budget=int(total_budget * 0.25),
            retriever=retriever,
        ))

    return await assemble(
        sections,
        total_budget=total_budget,
        counter=counter,
        query=content if content else diff,
    )


def _hash_violation(v: dict) -> str:
    """Stable fallback key for violations that lack an `id` field."""
    import hashlib
    key = f"{v.get('file_path')}:{v.get('line_start')}:{v.get('category')}:{v.get('description')}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/context/test_recipes.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/context/recipes.py tests/context/test_recipes.py
git commit -m "feat(context): add build_review_context recipe"
```

---

### Task 9: `build_research_context`

**Files:**
- Modify: `ollama_sentinel/context/recipes.py`
- Modify: `tests/context/test_recipes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/context/test_recipes.py`:
```python
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
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/context/test_recipes.py -v`
Expected: ImportError for `build_research_context`.

- [ ] **Step 3: Implement `build_research_context`**

Append to `ollama_sentinel/context/recipes.py`:
```python
def _format_impact_report(impact) -> str:
    """Inline impact report formatter (duplicated from research_agent.tools.synthesis
    to keep the context package independent of the research_agent package).

    `impact` is duck-typed: it must have .items (iterable of objects with
    .file_path, .line_number, .pattern, .severity, .action) and .affected_files.
    """
    items = getattr(impact, "items", []) or []
    affected = getattr(impact, "affected_files", []) or []
    high = [it for it in items if getattr(it, "severity", "") == "HIGH"]
    medium = [it for it in items if getattr(it, "severity", "") == "MEDIUM"]
    low = [it for it in items if getattr(it, "severity", "") == "LOW"]

    lines: List[str] = [
        f"{len(items)} call sites across {len(affected)} files",
        "",
    ]
    if high:
        lines.append("HIGH SEVERITY (breaking):")
        for it in high:
            lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {it.action}")
        lines.append("")
    if medium:
        lines.append("MEDIUM SEVERITY (deprecated):")
        for it in medium:
            action = it.action or "Review usage"
            lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {action}")
        lines.append("")
    if low:
        lines.append("LOW SEVERITY (changed):")
        for it in low:
            action = it.action or "Monitor for changes"
            lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {action}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _content_item_to_context_item(src) -> ContextItem:
    """Convert a research_agent ContentItem (duck-typed) into a ContextItem."""
    url = getattr(src, "url", "") or ""
    title = getattr(src, "title", "") or ""
    content = getattr(src, "content", "") or ""
    text = f"SOURCE: {url}\n{title}\n---\n{content}"
    import hashlib
    key = f"source:{hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]}" if url else f"source:{hashlib.sha1(content[:256].encode('utf-8')).hexdigest()[:16]}"
    return ContextItem(text=text, embed_key=key)


async def build_research_context(
    *,
    query: str,
    web_sources: Sequence,
    code_results: Optional[str],
    impact,  # Optional[ImpactAnalysis] — duck-typed to keep packages decoupled
    counter: TokenCounter,
    total_budget: int,
    retriever: Retriever,
) -> str:
    """Research-agent recipe — replaces the 4000-char truncation in synthesis."""
    sections: List[Section] = []

    if impact is not None and getattr(impact, "items", None):
        sections.append(Section(
            name="IMPACT ANALYSIS",
            items=[_format_impact_report(impact)],
            priority=Priority.MUST_FIT,
            soft_budget=int(total_budget * 0.30),
            truncate="tail",
        ))

    if code_results:
        sections.append(Section(
            name="CODE CONTEXT",
            items=[code_results],
            priority=Priority.MUST_FIT,
            soft_budget=int(total_budget * 0.20),
            truncate="tail",
        ))

    if web_sources:
        sections.append(Section(
            name="WEB SOURCES",
            items=[_content_item_to_context_item(s) for s in web_sources],
            priority=Priority.OPTIONAL,
            soft_budget=int(total_budget * 0.45),
            retriever=retriever,
        ))

    return await assemble(
        sections, total_budget=total_budget, counter=counter, query=query,
    )
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/context/test_recipes.py -v`
Expected: all tests pass.

- [ ] **Step 5: Update package init**

Modify `ollama_sentinel/context/__init__.py` — add:
```python
from ollama_sentinel.context.recipes import build_research_context, build_review_context
```
Append `"build_research_context"`, `"build_review_context"` to `__all__`.

- [ ] **Step 6: Run full context suite**

Run: `pytest tests/context/ -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add ollama_sentinel/context/ tests/context/test_recipes.py
git commit -m "feat(context): add build_research_context recipe"
```

---

## Phase 4 — Config additions

### Task 10: Extend `models.py`

**Files:**
- Modify: `ollama_sentinel/models.py`
- Modify: `tests/conftest.py` (no functional changes — new fields have defaults)
- Test: `tests/test_models.py` (existing file; add new cases)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py` (if the file doesn't exist, create it with minimal imports):
```python
import pytest

from ollama_sentinel.models import (
    EmbeddingConfig,
    MemoryConfig,
    OllamaConfig,
    OllamaModelConfig,
    ProcessingConfig,
    SentinelConfig,
    WatchConfig,
)


class TestEmbeddingConfig:
    def test_defaults(self):
        cfg = EmbeddingConfig()
        assert cfg.enabled is True
        assert cfg.model == "nomic-embed-text"


class TestOllamaModelConfigTokenFields:
    def test_context_window_default(self):
        cfg = OllamaModelConfig(name="m", system_prompt="p")
        assert cfg.context_window == 8192
        assert cfg.output_reserve_tokens == 2000


class TestMemoryConfigSemanticFields:
    def test_neighbor_k_and_semantic_recall_defaults(self):
        cfg = MemoryConfig()
        assert cfg.neighbor_k == 10
        assert cfg.semantic_recall is True


class TestProcessingConfigDeprecation:
    def test_legacy_fields_are_accepted_and_warn_once(self, caplog):
        with caplog.at_level("WARNING"):
            ProcessingConfig(max_chars_per_chunk=99, overlap_chars=7)
        assert "deprecated" in caplog.text.lower() or "ignored" in caplog.text.lower()


class TestSentinelConfigEmbeddingField:
    def test_embedding_defaults_populate(self, tmp_path):
        cfg = SentinelConfig(
            watch=WatchConfig(directory=str(tmp_path)),
            ollama=OllamaConfig(
                host="http://localhost:11434",
                models={"default": OllamaModelConfig(name="m", system_prompt="p")},
            ),
        )
        assert isinstance(cfg.embedding, EmbeddingConfig)
        assert cfg.embedding.enabled is True
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_models.py -v`
Expected: ImportError for `EmbeddingConfig` and attribute errors.

- [ ] **Step 3: Modify `models.py`**

Open `ollama_sentinel/models.py` and apply these changes:

**(a) Extend `OllamaModelConfig`** (insert after existing fields):
```python
class OllamaModelConfig(BaseModel):
    """Configuration for a specific Ollama model."""
    name: str
    system_prompt: str
    temperature: float = 0.1
    top_p: float = 0.9
    max_tokens: Optional[int] = None
    context_window: int = 8192
    output_reserve_tokens: int = 2000
```

**(b) Add `EmbeddingConfig`** (new class, near `MemoryConfig`):
```python
class EmbeddingConfig(BaseModel):
    """Configuration for the Ollama embedding backend."""
    enabled: bool = True
    model: str = "nomic-embed-text"
```

**(c) Extend `MemoryConfig`**:
```python
class MemoryConfig(BaseModel):
    """Configuration for violation memory."""
    enabled: bool = True
    db_path: str = ".ollama_reviews/memory.db"
    neighbor_k: int = 10
    semantic_recall: bool = True
```

**(d) Deprecate legacy fields on `ProcessingConfig`** — add a `model_validator`:
```python
import logging
from pydantic import model_validator

log = logging.getLogger("ollama-sentinel")

_PROCESSING_DEPRECATION_LOGGED = False


class ProcessingConfig(BaseModel):
    """Configuration for file processing."""
    max_concurrent_reviews: int = 3
    max_concurrent_chunks_per_file: int = 2
    git_diff_mode: bool = False

    model_config = {"extra": "allow"}  # tolerate legacy keys without failing

    @model_validator(mode="after")
    def _warn_legacy_fields(self):
        global _PROCESSING_DEPRECATION_LOGGED
        legacy = [k for k in ("max_chars_per_chunk", "overlap_chars")
                  if k in self.__pydantic_extra__]
        if legacy and not _PROCESSING_DEPRECATION_LOGGED:
            log.warning(
                "ProcessingConfig fields %s are deprecated and ignored; "
                "chunk sizing now derives from OllamaModelConfig.context_window. "
                "Remove these fields from your YAML to silence this warning.",
                legacy,
            )
            _PROCESSING_DEPRECATION_LOGGED = True
        return self
```

**(e) Add `embedding: EmbeddingConfig` to `SentinelConfig`**:
```python
class SentinelConfig(BaseModel):
    """Main application configuration."""
    watch: WatchConfig
    ollama: OllamaConfig
    processing: ProcessingConfig = ProcessingConfig()
    output: OutputConfig = OutputConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    memory: MemoryConfig = MemoryConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/test_models.py -v`
Expected: all new tests pass.

- [ ] **Step 5: Run full suite to check for regressions**

Run: `pytest tests/ -x -q`
Expected: all pre-existing tests still pass. Any test that constructs `ProcessingConfig(max_chars_per_chunk=...)` still works because of `extra="allow"`.

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/models.py tests/test_models.py
git commit -m "feat(models): add EmbeddingConfig and token-budget fields"
```

---

## Phase 5 — ViolationDB migration

### Task 11: Add `embed_text` column + `_migrate()` + `get_all_unresolved()`

**Files:**
- Modify: `ollama_sentinel/violation_db.py`
- Modify: `tests/test_violation_db.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_violation_db.py`:
```python
from ollama_sentinel.violation_db import ViolationDB, Finding


class TestMigration:
    def test_embed_text_column_added_on_init(self, tmp_path):
        db_path = str(tmp_path / "v.db")
        db = ViolationDB(db_path)
        cur = db._conn.execute("PRAGMA table_info(findings)")
        cols = {row[1] for row in cur.fetchall()}
        assert "embed_text" in cols
        db.close()

    def test_migrate_is_idempotent(self, tmp_path):
        db_path = str(tmp_path / "v.db")
        db1 = ViolationDB(db_path)
        db1.close()
        # Second init should not raise.
        db2 = ViolationDB(db_path)
        cur = db2._conn.execute("PRAGMA table_info(findings)")
        cols = {row[1] for row in cur.fetchall()}
        assert "embed_text" in cols
        db2.close()

    def test_backfill_populates_embed_text_for_existing_rows(self, tmp_path):
        db_path = str(tmp_path / "v.db")
        # Simulate a pre-migration DB by manually creating the old schema.
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                description TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                resolved INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO findings(file_path, line_start, line_end, category, severity, "
            "description, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("a.py", 5, 5, "bug", "high", "null deref", "2026-01-01", "2026-01-01"),
        )
        conn.commit()
        conn.close()

        db = ViolationDB(db_path)
        rows = db._conn.execute("SELECT embed_text FROM findings").fetchall()
        assert rows[0][0] is not None
        assert "null deref" in rows[0][0]
        db.close()


class TestGetAllUnresolved:
    def test_returns_rows_across_files(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        db.persist_findings("a.py", [Finding("a.py", 1, 1, "bug", "low", "x")])
        db.persist_findings("b.py", [Finding("b.py", 2, 2, "perf", "medium", "y")])
        rows = db.get_all_unresolved()
        files = {r["file_path"] for r in rows}
        assert files == {"a.py", "b.py"}
        db.close()


class TestEmbedTextOnInsert:
    def test_new_findings_have_embed_text(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        db.persist_findings("a.py", [
            Finding("a.py", 10, 12, "security", "critical", "plaintext password"),
        ])
        row = db._conn.execute("SELECT embed_text FROM findings WHERE id=1").fetchone()
        assert row[0] is not None
        assert "plaintext password" in row[0]
        assert "[critical]" in row[0]
        db.close()
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_violation_db.py -v -k "Migration or GetAllUnresolved or EmbedTextOnInsert"`
Expected: FAIL — no migration code yet.

- [ ] **Step 3: Modify `violation_db.py`**

Open `ollama_sentinel/violation_db.py`. Change `__init__`:

```python
def __init__(self, db_path: str) -> None:
    self._conn = sqlite3.connect(db_path)
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute(self._CREATE_TABLE)
    self._conn.commit()
    self._migrate()
```

Add the `_migrate` method:

```python
def _migrate(self) -> None:
    """Idempotent migration: add the embed_text column and backfill values."""
    try:
        cur = self._conn.execute("PRAGMA table_info(findings)")
        cols = {row[1] for row in cur.fetchall()}
        if "embed_text" not in cols:
            self._conn.execute("ALTER TABLE findings ADD COLUMN embed_text TEXT")
            self._conn.execute(
                """
                UPDATE findings
                SET embed_text =
                    '[' || severity || '] ' || category || ' at ' ||
                    file_path || ':' || line_start || ': ' || description
                WHERE embed_text IS NULL
                """
            )
            self._conn.commit()
    except sqlite3.DatabaseError as e:
        # Log and leave the DB unchanged; callers will fall back gracefully.
        import logging
        logging.getLogger("ollama-sentinel").error("ViolationDB migration failed: %s", e)
```

Update `_CREATE_TABLE` to include the new column for fresh databases:
```python
_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS findings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path       TEXT    NOT NULL,
        line_start      INTEGER NOT NULL,
        line_end        INTEGER NOT NULL,
        category        TEXT    NOT NULL,
        severity        TEXT    NOT NULL,
        description     TEXT    NOT NULL,
        first_seen      TEXT    NOT NULL,
        last_seen       TEXT    NOT NULL,
        occurrence_count INTEGER NOT NULL DEFAULT 1,
        resolved        INTEGER NOT NULL DEFAULT 0,
        embed_text      TEXT
    )
"""
```

Populate `embed_text` on insert — change the INSERT branch inside `persist_findings`:
```python
else:
    embed_text = (
        f"[{f.severity}] {f.category} at {f.file_path}:{f.line_start}: {f.description}"
    )
    cur.execute(
        """
        INSERT INTO findings
            (file_path, line_start, line_end, category,
             severity, description, first_seen, last_seen, embed_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f.file_path, f.line_start, f.line_end, f.category,
            f.severity, f.description, now, now, embed_text,
        ),
    )
```

Add `get_all_unresolved`:
```python
def get_all_unresolved(self) -> List[dict]:
    """Return every unresolved finding across all files."""
    cur = self._conn.execute("SELECT * FROM findings WHERE resolved = 0")
    return self._rows_to_dicts(cur)
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/test_violation_db.py -v`
Expected: all tests pass (new and pre-existing).

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/violation_db.py tests/test_violation_db.py
git commit -m "feat(violation_db): add embed_text column and migration"
```

---

### Task 12: `get_neighbors_by_similarity`

**Files:**
- Modify: `ollama_sentinel/violation_db.py`
- Modify: `tests/test_violation_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_violation_db.py`:
```python
from ollama_sentinel.context.embeddings import EmbeddingUnavailable


class _MapEmbedder:
    """Fake embedder: text->vector lookup; raises for unknown keys."""
    def __init__(self, mapping):
        self._m = mapping

    async def embed(self, text, *, cache_key=None):
        for needle, vec in self._m.items():
            if needle in text or needle == cache_key:
                return vec
        raise EmbeddingUnavailable(f"no mapping for {text!r}")


class TestGetNeighborsBySimilarity:
    async def test_returns_top_k_by_cosine(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        db.persist_findings("a.py", [
            Finding("a.py", 1, 1, "security", "high", "sql injection via string format"),
            Finding("a.py", 2, 2, "style", "low", "line too long"),
            Finding("a.py", 3, 3, "perf", "medium", "nested loop over items"),
        ])
        embedder = _MapEmbedder({
            "query_vec": [1.0, 0.0, 0.0],
            "sql injection": [1.0, 0.0, 0.0],        # cosine 1.0
            "nested loop": [0.5, 0.5, 0.0],          # cosine 0.707
            "line too long": [0.0, 1.0, 0.0],        # cosine 0.0
        })
        rows = await db.get_neighbors_by_similarity(
            query_text="query_vec", embedder=embedder, k=2,
        )
        assert len(rows) == 2
        descriptions = [r["description"] for r in rows]
        assert descriptions[0] == "sql injection via string format"
        assert descriptions[1] == "nested loop over items"
        db.close()

    async def test_returns_empty_when_embedding_unavailable(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        db.persist_findings("a.py", [Finding("a.py", 1, 1, "bug", "low", "x")])
        # Embedder always raises.
        class _BadEmbedder:
            async def embed(self, text, *, cache_key=None):
                raise EmbeddingUnavailable("down")
        rows = await db.get_neighbors_by_similarity(
            query_text="anything", embedder=_BadEmbedder(), k=10,
        )
        assert rows == []
        db.close()
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_violation_db.py -v -k "GetNeighbors"`
Expected: AttributeError.

- [ ] **Step 3: Implement `get_neighbors_by_similarity`**

Append to `ollama_sentinel/violation_db.py`:
```python
async def get_neighbors_by_similarity(
    self,
    query_text: str,
    embedder,
    k: int = 10,
) -> List[dict]:
    """Rank all unresolved findings by cosine similarity to query_text.

    `embedder` is duck-typed (OllamaEmbedder or any object with an async
    `embed(text, *, cache_key=None) -> list[float]`). Returns [] if the
    embedder cannot embed the query.
    """
    import hashlib
    import math
    from ollama_sentinel.context.embeddings import EmbeddingUnavailable

    rows = self.get_all_unresolved()
    if not rows:
        return []

    query_key = f"query:{hashlib.sha256(query_text.encode('utf-8')).hexdigest()}"
    try:
        query_vec = await embedder.embed(query_text, cache_key=query_key)
    except EmbeddingUnavailable:
        return []

    import asyncio

    async def _embed_row(row):
        embed_text = row.get("embed_text") or (
            f"[{row['severity']}] {row['category']} at {row['file_path']}:"
            f"{row['line_start']}: {row['description']}"
        )
        try:
            vec = await embedder.embed(embed_text, cache_key=f"finding:{row['id']}")
        except EmbeddingUnavailable:
            vec = None
        return row, vec

    pairs = await asyncio.gather(*(_embed_row(r) for r in rows))
    scored = []
    for row, vec in pairs:
        if vec is None:
            continue
        dot = sum(a * b for a, b in zip(query_vec, vec))
        na = math.sqrt(sum(a * a for a in query_vec))
        nb = math.sqrt(sum(b * b for b in vec))
        score = dot / (na * nb) if na and nb else 0.0
        scored.append((score, row))

    scored.sort(key=lambda p: p[0], reverse=True)
    return [row for _score, row in scored[:k]]
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/test_violation_db.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/violation_db.py tests/test_violation_db.py
git commit -m "feat(violation_db): add get_neighbors_by_similarity"
```

---

## Phase 6 — Sentinel integration

### Task 13: Wire `FileProcessor` to the review recipe

**Files:**
- Modify: `ollama_sentinel/processor.py`
- Modify: `ollama_sentinel/utils.py`
- Modify: `tests/test_processor.py`

- [ ] **Step 1: Read the current tests that pin `format_prompt` behavior**

Run: `grep -n "format_prompt\|_get_prior" tests/test_processor.py`

Expected output shows the tests to update (each must be changed from sync to async).

- [ ] **Step 2: Update `FileProcessor` in `processor.py`**

Find the `__init__` method and extend it to build a shared `TokenCounter`, `Cache`, and `OllamaEmbedder`:

```python
def __init__(self, config: SentinelConfig, violation_db=None):
    self.config = config
    self.watch_dir = pathlib.Path(config.watch.directory).resolve()
    self.output_dir = self.watch_dir / config.output.directory
    self.ollama_client = OllamaClient(config.ollama.model_dump())
    self.violation_db = violation_db
    self.repo = None

    # Context-assembly dependencies (Task 13).
    from ollama_sentinel.context import (
        NullRetriever,
        OllamaEmbedder,
        SemanticRetriever,
        TokenCounter,
    )
    from research_agent.utils.cache import Cache  # reuse the diskcache wrapper

    self.counter = TokenCounter()
    if config.embedding.enabled and config.memory.semantic_recall:
        self._cache = Cache(cache_dir=str(self.output_dir / ".embed_cache"))
        self.embedder = OllamaEmbedder(
            host=config.ollama.host,
            model=config.embedding.model,
            cache=self._cache,
        )
        self.retriever = SemanticRetriever(embedder=self.embedder)
    else:
        self._cache = None
        self.embedder = None
        self.retriever = NullRetriever()

    # Token budget for review prompts.
    default_model = config.ollama.models["default"]
    self.total_budget = max(
        1024,
        default_model.context_window - default_model.output_reserve_tokens,
    )

    if config.processing.git_diff_mode:
        try:
            self.repo = git.Repo(self.watch_dir, search_parent_directories=True)
            log.info(f"Git repository found at {self.repo.working_dir}")
        except git.InvalidGitRepositoryError:
            log.warning("Git repository not found, disabling git_diff_mode")
            self.config.processing.git_diff_mode = False
```

Update `close`:
```python
async def close(self):
    """Close clients."""
    await self.ollama_client.close()
    if self.embedder is not None:
        await self.embedder.close()
```

Replace the body of `chunk_content` with token-based chunking:
```python
def chunk_content(self, content: str, file_type: str) -> List[str]:
    """Split content into chunks using a token budget."""
    from ollama_sentinel.context.assembler import chunk_by_lines

    # Reserve 30% of the context window for the review recipe's overhead
    # (header, prior violations block). Chunks target ~70% of the budget.
    chunk_budget = max(256, int(self.total_budget * 0.70))
    overlap = max(32, chunk_budget // 20)
    return chunk_by_lines(
        content,
        counter=self.counter,
        max_tokens=chunk_budget,
        overlap_tokens=overlap,
    )
```

Replace `format_prompt` with an async wrapper that calls the recipe:
```python
async def format_prompt(
    self,
    file_change: FileChange,
    chunk_text: Optional[str] = None,
    chunk_index: int = 0,
    total_chunks: int = 1,
    prior_violations: Optional[List[dict]] = None,
) -> str:
    """Format the review prompt via the shared context recipe."""
    from ollama_sentinel.context import build_review_context

    rel_path = str(file_change.path.relative_to(self.watch_dir))
    chunk_info = f" (Part {chunk_index + 1}/{total_chunks})" if total_chunks > 1 else ""
    content = chunk_text if chunk_text is not None else file_change.content
    return await build_review_context(
        file_rel_path=rel_path,
        file_type=file_change.file_type,
        content=content,
        diff=file_change.diff,
        chunk_info=chunk_info,
        prior_violations=prior_violations or [],
        counter=self.counter,
        total_budget=self.total_budget,
        retriever=self.retriever,
    )
```

Rename and update `_get_prior_violations`:
```python
async def _get_ranked_prior_violations(
    self, file_path: pathlib.Path, *, file_content: Optional[str]
) -> Optional[List[dict]]:
    """Fetch prior violations, ranked semantically when possible."""
    if not self.violation_db:
        return None
    try:
        if (self.config.memory.semantic_recall
                and self.embedder is not None
                and file_content):
            violations = await self.violation_db.get_neighbors_by_similarity(
                query_text=file_content,
                embedder=self.embedder,
                k=self.config.memory.neighbor_k,
            )
        else:
            rel = str(file_path.relative_to(self.watch_dir))
            violations = await asyncio.to_thread(
                self.violation_db.get_unresolved, rel,
            )
        return violations if violations else None
    except Exception as e:
        log.warning("Failed to query prior violations (%s); continuing without them.", e)
        return None
```

Update every call to `format_prompt` and `_get_prior_violations` inside `generate_review` to be `await`-ed, and rename:

```python
async def generate_review(self, file_change: FileChange, model_role: str = "default") -> str:
    await asyncio.to_thread(self.prepare_file_content, file_change)
    prior = await self._get_ranked_prior_violations(
        file_change.path, file_content=file_change.content,
    )

    if file_change.diff is not None:
        prompt = await self.format_prompt(file_change, prior_violations=prior)
        return await self.ollama_client.generate_review(model_role, prompt)

    content = file_change.content
    if not content:
        prompt = await self.format_prompt(file_change, prior_violations=prior)
        return await self.ollama_client.generate_review(model_role, prompt)

    chunks = self.chunk_content(content, file_change.file_type)

    if len(chunks) == 1:
        prompt = await self.format_prompt(
            file_change, chunk_text=chunks[0], prior_violations=prior,
        )
        return await self.ollama_client.generate_review(model_role, prompt)

    async def review_chunk(chunk_idx, total_chunks):
        violations = prior if chunk_idx == 0 else None
        prompt = await self.format_prompt(
            file_change,
            chunk_text=chunks[chunk_idx],
            chunk_index=chunk_idx,
            total_chunks=total_chunks,
            prior_violations=violations,
        )
        return await self.ollama_client.generate_review(model_role, prompt)

    max_concurrent_chunks = min(
        len(chunks), self.config.processing.max_concurrent_chunks_per_file,
    )
    chunk_semaphore = asyncio.Semaphore(max_concurrent_chunks)

    async def process_chunk_with_semaphore(chunk_idx, total_chunks):
        async with chunk_semaphore:
            return await review_chunk(chunk_idx, total_chunks)

    tasks = [
        process_chunk_with_semaphore(i, len(chunks)) for i in range(len(chunks))
    ]
    reviews = await asyncio.gather(*tasks)

    combined = "\n\n".join([
        f"## Part {i+1}/{len(chunks)}\n\n{review}"
        for i, review in enumerate(reviews)
    ])
    return f"# Combined Review for {file_change.path.name}\n\n{combined}"
```

Remove the static `_format_violations` method — it's superseded by `_render_violation` in the recipe.

- [ ] **Step 3: Keep `utils.chunk_content_by_lines` as a back-compat shim**

Edit `ollama_sentinel/utils.py` — replace the `chunk_content_by_lines` function body with a shim:
```python
def chunk_content_by_lines(content: str, max_chars: int, overlap: int) -> List[str]:
    """Deprecated: use ollama_sentinel.context.assembler.chunk_by_lines.

    This shim preserves the old char-based API for any remaining callers.
    """
    import logging
    logging.getLogger("ollama-sentinel").warning(
        "chunk_content_by_lines is deprecated; use context.assembler.chunk_by_lines."
    )
    if len(content) <= max_chars:
        return [content]
    # Original implementation kept verbatim for callers still passing char counts.
    chunks = []
    lines = content.splitlines(True)
    current_chunk = []
    current_size = 0
    for line in lines:
        line_size = len(line)
        if current_size + line_size > max_chars and current_chunk:
            chunks.append("".join(current_chunk))
            overlap_size = 0
            overlap_chunk = []
            for prev_line in reversed(current_chunk):
                if overlap_size + len(prev_line) > overlap:
                    break
                overlap_chunk.insert(0, prev_line)
                overlap_size += len(prev_line)
            current_chunk = overlap_chunk
            current_size = overlap_size
        current_chunk.append(line)
        current_size += line_size
    if current_chunk:
        chunks.append("".join(current_chunk))
    return chunks
```

(Note: the function body is unchanged from its original form; only the docstring and a deprecation warning are added.)

- [ ] **Step 4: Update `tests/test_processor.py` for async `format_prompt`**

Find every test that calls `processor.format_prompt(...)` and change:
```python
prompt = processor.format_prompt(...)
```
to:
```python
prompt = await processor.format_prompt(...)
```

For each test that previously asserted the exact shape of the prompt (prior violations + file block), assert the new shape:
- `"FILE: <rel_path>"` appears.
- Backtick-fenced file type or `diff` block appears.
- If prior violations were passed, `"PRIOR UNRESOLVED ISSUES"` appears.

No new assertions are required beyond substring checks. Do not over-pin the exact whitespace — the recipe owns that.

For any test that previously referenced `_format_violations` (static), delete those tests — the helper is gone.

For any test that accessed `processor._get_prior_violations`, update to `processor._get_ranked_prior_violations(path, file_content=...)`.

- [ ] **Step 5: Run — expect pass**

Run: `pytest tests/test_processor.py -v`
Expected: all tests pass.

Run: `pytest tests/ -x -q`
Expected: entire suite still passes.

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/processor.py ollama_sentinel/utils.py tests/test_processor.py
git commit -m "feat(sentinel): use ContextBuilder recipe for review prompts"
```

---

### Task 14: End-of-phase test gate

- [ ] **Step 1: Run the whole suite**

Run: `pytest tests/ -v`
Expected: all tests pass. Total duration still well under 3 seconds.

- [ ] **Step 2: Dry-run the CLI to ensure startup still works**

Run: `ollama-sentinel --help`
Expected: usage output, no errors.

Run: `ollama-sentinel init` in a clean `tmp_test_dir`, then inspect the generated YAML to confirm `embedding` and the new memory fields are present (or at least that defaults apply at load time). If `init` writes the YAML, confirm new sections appear.

- [ ] **Step 3: If init does not emit new fields, add them**

Edit `ollama_sentinel/config.py` `create_default_config` to emit the new sections:
```yaml
embedding:
  enabled: true
  model: nomic-embed-text
memory:
  enabled: true
  db_path: .ollama_reviews/memory.db
  neighbor_k: 10
  semantic_recall: true
```

Only make this change if `init` output currently lacks these sections. Add a small test confirming the keys are present in the generated config.

- [ ] **Step 4: Commit (if step 3 changed anything)**

```bash
git add ollama_sentinel/config.py tests/
git commit -m "feat(config): emit new embedding/memory defaults on init"
```

---

## Phase 7 — Research-agent synthesis integration

### Task 15: Wire `SynthesisTool` to `build_research_context`

**Files:**
- Modify: `research_agent/tools/synthesis.py`
- Modify: `research_agent/core/workflow.py` (SynthesisTool construction only)
- Modify: `research_agent/core/config.py` (if used — otherwise the TOML / dict carries the new key)
- Modify: `tests/test_research_agent.py` (existing tests assert template vars — update to `assembled_context`)

- [ ] **Step 1: Update `SynthesisTool` constructor**

Edit `research_agent/tools/synthesis.py`. Add imports:
```python
from ollama_sentinel.context import (
    NullRetriever,
    OllamaEmbedder,
    SemanticRetriever,
    TokenCounter,
    build_research_context,
)
```

Update `__init__` to accept optional context-assembly dependencies:
```python
def __init__(
    self,
    openai_api_key: str,
    model_name: str = "gpt-4o-preview",
    temperature: float = 0.1,
    *,
    total_budget: int = 12000,
    embedder: Optional[OllamaEmbedder] = None,
):
    self.openai_api_key = openai_api_key
    self.model_name = model_name
    self.temperature = temperature
    self.total_budget = total_budget
    self.counter = TokenCounter()
    self.retriever = SemanticRetriever(embedder) if embedder is not None else NullRetriever()
    self.llm = ChatOpenAI(
        model=model_name, temperature=temperature, api_key=openai_api_key,
    )

    self.main_template = compiler.compile("""
<system>
You are a top-tier research synthesis system that creates comprehensive, accurate answers by combining information from web sources and code repositories.

GUIDELINES:
1. Analyze and integrate information from multiple sources
2. Maintain critical thinking through fact triangulation
3. Create well-structured answers with appropriate sections and formatting
4. Use clear, precise language with appropriate technical depth
5. Include direct quotes from sources sparingly and with attribution
6. Structure your answer around the key concepts, not source by source
7. Cite all information with inline numbered references [1], [2], etc.
8. Provide a complete REFERENCES section at the end listing all sources
9. Assess confidence in your final answer on a scale of 0-1
</system>

QUERY: {{query}}

{{assembled_context}}

TASK: Synthesize a comprehensive, accurate answer that integrates web information with code context.
Include inline citations [1], [2], etc. and a REFERENCES section at the end.
Assess your confidence in the final answer on a scale of 0-1.
""")
```

Delete `_preprocess_sources` entirely (the 4000-char truncator).

Rewrite `synthesize` to call the recipe:
```python
def synthesize(
    self,
    query: str,
    sources: List[ContentItem],
    code_context: Optional[str] = None,
    impact_analysis: Optional[ImpactAnalysis] = None,
) -> Dict[str, Any]:
    """Synthesize an answer from sources and code context."""
    logger.info(f"Synthesizing answer for query: {query}")

    # Structured impact output short-circuit (unchanged).
    if impact_analysis is not None and impact_analysis.items:
        report = self.format_impact_report(impact_analysis)
        return {"answer": report, "references": [], "confidence": 0.9}

    try:
        # Build the assembled context via the shared recipe.
        # The recipe is async; this method is sync to preserve the existing
        # LangGraph node contract. Run the coroutine on a fresh loop.
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            assembled = loop.run_until_complete(
                build_research_context(
                    query=query,
                    web_sources=sources,
                    code_results=code_context,
                    impact=impact_analysis,
                    counter=self.counter,
                    total_budget=self.total_budget,
                    retriever=self.retriever,
                )
            )
        finally:
            loop.close()

        prompt = self.main_template({"query": query, "assembled_context": assembled})
        response = self.llm.invoke(prompt)
        content = response.content

        references = self._extract_references(content)
        confidence = self._extract_confidence(content)
        return {"answer": content, "references": references, "confidence": confidence}

    except Exception as e:
        logger.error(f"Error synthesizing answer: {e}")
        return {"answer": f"Error synthesizing answer: {str(e)}", "references": [], "confidence": 0.0}
```

- [ ] **Step 2: Update `workflow.py` to construct `SynthesisTool` with the new kwargs**

Find the `synthesis_tool = SynthesisTool(...)` construction in `research_agent/core/workflow.py` and extend it:
```python
# Optional: build a shared embedder for semantic ranking of web sources.
_embedder = None
if config.get("embedding", {}).get("enabled", False):
    from ollama_sentinel.context import OllamaEmbedder
    _embedder = OllamaEmbedder(
        host=config["embedding"].get("host", "http://localhost:11434"),
        model=config["embedding"].get("model", "nomic-embed-text"),
        cache=cache,
    )

synthesis_tool = SynthesisTool(
    openai_api_key=openai_api_key,
    model_name=config["api"]["openai_model"],
    temperature=config["agent"]["synthesis_temperature"],
    total_budget=config["api"].get("synthesis_context_tokens", 12000),
    embedder=_embedder,
)
```

Record the embedder on `components` so downstream callers can close it:
```python
components["embedder"] = _embedder
```

- [ ] **Step 3: Update `research_agent/core/config.py` default TOML**

If `research_agent/core/config.py` holds default TOML values, add `synthesis_context_tokens = 12000` under `[api]`. If the defaults live in a separate `default_config.toml`, patch that. Also add a new `[embedding]` section with `enabled = false` (default off to avoid requiring an Ollama install for research-only users).

- [ ] **Step 4: Update affected tests in `tests/test_research_agent.py`**

Any test that patches or asserts `_preprocess_sources` must be deleted. Any test that asserts the template's handlebars output shape must switch to asserting that `{{assembled_context}}` expands into the section strings (`"WEB SOURCES:"` etc.).

If `ImpactAnalysis.items` is empty, the existing narrative path still runs — assert the call now includes the assembled context string by patching `build_research_context` and checking it was invoked with the right args.

- [ ] **Step 5: Run — expect pass**

Run: `pytest tests/test_research_agent.py -v`
Expected: all tests pass.

Run: `pytest tests/ -x -q`
Expected: entire suite passes.

- [ ] **Step 6: Commit**

```bash
git add research_agent/tools/synthesis.py research_agent/core/workflow.py research_agent/core/config.py tests/test_research_agent.py
git commit -m "feat(research_agent): use ContextBuilder recipe for synthesis"
```

---

## Phase 8 — Full suite + docs

### Task 16: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the "Architecture" and "Key modules" sections**

Open `CLAUDE.md`. In the "Key modules" table add:
```
| `ollama_sentinel/context/assembler.py` | Section dataclasses + `assemble()` — pure, token-budgeted context assembly |
| `ollama_sentinel/context/embeddings.py` | `OllamaEmbedder` — async /api/embeddings client with cache-backed vectors |
| `ollama_sentinel/context/retrievers.py` | `NullRetriever`, `SemanticRetriever` for ranking context items |
| `ollama_sentinel/context/recipes.py` | `build_review_context`, `build_research_context` — named recipes for the two consumers |
| `ollama_sentinel/context/tokens.py` | `TokenCounter` — tiktoken wrapper with char-fallback |
```

Replace the "Known Issues / Next Session Breadcrumbs" bullet about `EnhancedMemoryStore.find_similar_*` with:
```
- `ViolationDB` now supports semantic recall via `get_neighbors_by_similarity`,
  backed by Ollama embeddings (`nomic-embed-text` by default). Requires
  `ollama pull nomic-embed-text` once; degrades to exact-path recall if
  embeddings are unavailable.
- `EnhancedMemoryStore.find_similar_*` still uses token-overlap (deferred —
  see docs/superpowers/plans/2026-04-16-context-builder.md Phase 9 follow-up).
```

Under "Known Issues", add:
```
- `ollama-sentinel run` requires `ollama pull nomic-embed-text` once on first
  use (or set `memory.semantic_recall: false` to fall back to the legacy
  exact-path recall).
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document ContextBuilder modules and embedding prereq"
```

---

### Task 17: Final gate — full suite, manual smoke, summary

- [ ] **Step 1: Run the whole suite**

Run: `pytest tests/ -v`
Expected: all tests pass (baseline ~247 + ~30 new = ~277).

- [ ] **Step 2: Measure**

Run: `pytest tests/ -q`
Expected: total runtime under 3 seconds.

- [ ] **Step 3: Manual smoke — OPTIONAL, requires a running Ollama**

This step is informational. If a local Ollama with `nomic-embed-text` and a chat model (e.g. `llama3`) is available:
```bash
ollama pull nomic-embed-text
ollama-sentinel init
ollama-sentinel review ollama_sentinel/processor.py -m default
```
Expected: review output is generated without errors; the `.ollama_reviews/.embed_cache/` directory is populated on the second run.

If Ollama is not available, this step is skipped — the unit suite has fully exercised every code path with mocks.

- [ ] **Step 4: Write a short completion summary**

Append a single line to `CLAUDE.md`'s breadcrumbs:
```
- 2026-04-16: ContextBuilder landed (plan: docs/superpowers/plans/2026-04-16-context-builder.md). Prompt assembly + violation memory are now embedding-ranked and token-budgeted.
```

- [ ] **Step 5: Commit the final breadcrumb**

```bash
git add CLAUDE.md
git commit -m "docs: record ContextBuilder completion in breadcrumbs"
```

---

## Phase 9 (optional follow-up) — `EnhancedMemoryStore` semantic ranking

Deferred from the main plan to keep the blast radius tight. If/when picked up:

1. Add async `find_similar_queries_semantic(text, retriever)` and
   `find_similar_webpages_semantic(text, retriever)` methods on
   `EnhancedMemoryStore`. Keep the existing sync token-overlap methods as
   fallbacks.
2. In `research_agent/core/workflow.py`'s `analyze` node, use the same
   `asyncio.new_event_loop` pattern that already exists in `read` to invoke
   the semantic methods.
3. Add tests that parallel the existing token-overlap tests with a fake
   embedder.

Not a prerequisite for any other task. Safe to ship the main plan without it.

---

## Self-review notes

Applied during plan writing:

- **Spec coverage:** Goals 1–3 from the spec map to Phases 1–3 (shared module), Phase 7 (research agent), Phases 5–6 (violation DB + sentinel), and Phase 8 (docs). Error-handling table maps to test cases in Tasks 3, 5, 6, 7, 11, 12, 15. Non-goals (pluggable backend, vector DB, streaming, VSCode) are not implemented, as specified.
- **Placeholder scan:** No "TBD", "TODO", "implement later", or "similar to Task N" references remain. Every code block is complete and self-contained.
- **Type consistency:** `ContextItem(text, embed_key)`, `Section(name, items, priority, soft_budget, retriever, truncate)`, `Retriever.rank(items, query)`, `OllamaEmbedder.embed(text, *, cache_key)`, `build_review_context(...)`, `build_research_context(...)` — signatures match across tasks.
- **Scope:** Phase 9 is explicitly carved out as optional so the main plan is a single coherent deliverable.
