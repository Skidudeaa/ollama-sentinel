# ContextBuilder — Design Spec

**Date:** 2026-04-16
**Status:** Approved (brainstorm complete, pending implementation-plan)
**Author:** brainstorm session between user and Claude
**Scope:** New `ollama_sentinel/context/` package shared by the sentinel and the
research agent. Replaces ad-hoc prompt assembly in `processor.format_prompt` and
`SynthesisTool._preprocess_sources`, and retrofits `ViolationDB` with semantic
retrieval. Inspired by Phind 0.25.4's sectioned, token-budgeted,
embedding-ranked context assembler.

---

## Goals

1. One shared module that both the sentinel and the research agent use to
   assemble prompt context, with per-section token budgets and semantic
   ranking of items that have many candidates.
2. Close the known memory-store gap: replace token-overlap scoring in
   `EnhancedMemoryStore.find_similar_*` and exact-file-path matching in
   `ViolationDB.get_unresolved` with real Ollama-computed embeddings.
3. Never block a review or a research run when embedding infrastructure
   fails — every new path degrades to "old behavior minus the new ranking."

## Non-goals

- A pluggable embedding-backend abstraction. Day-one is Ollama-native only.
  A pluggable `EmbeddingBackend` protocol can be added later if a second
  backend is needed.
- A vector database (sqlite-vec, Weaviate, etc.). Embeddings live in the
  existing `Cache` (JSON-serialized diskcache). Cosine similarity runs in
  pure Python. Fine for the expected working set (<10k findings, <100 web
  sources per session).
- Streaming / incremental assembly. `assemble()` returns a complete string.
- A VSCode extension. Phind patterns are being ported into the Python stack,
  not into a new editor host.

## Constraints

- Python >= 3.10 (matches existing project).
- Async-first: the assembler is async throughout; retrievers are async;
  callers already run in an `asyncio` loop.
- Runtime adds exactly one new dep: `tiktoken`. Ollama HTTP continues to
  use the existing `httpx` client.
- Pydantic v2 validators for any new config models.

---

## Architecture

New package `ollama_sentinel/context/` — single source of truth for prompt
assembly.

```
ollama_sentinel/context/
├── __init__.py          # public: assemble, Section, Priority, ContextItem, recipes
├── assembler.py         # Section, Priority, ContextItem, assemble() — pure, no I/O
├── tokens.py            # TokenCounter wrapping tiktoken (cl100k_base)
├── embeddings.py        # OllamaEmbedder — httpx POST /api/embeddings, cache-backed
├── retrievers.py        # Retriever protocol, NullRetriever, SemanticRetriever
└── recipes.py           # build_review_context, build_research_context
```

**Separation rationale:**

- `assembler.py` has no I/O, no tokenizer instantiation, no embedding calls.
  It takes injected `counter` and pre-ranked items. Test-isolated.
- `embeddings.py` is the only module that speaks to Ollama's `/api/embeddings`.
  Reuses the existing `Cache` — no new storage dependency.
- `recipes.py` is the public seam for consumers. Neither sentinel nor research
  agent hand-assemble sections; they call a single recipe function each.
- The package lives inside `ollama_sentinel/` (not a new top-level package)
  because the sentinel is the primary consumer and the research agent already
  imports from the project root.

---

## Core primitives

Located in `assembler.py`. All dataclasses are frozen; the entrypoint is pure.

```python
class Priority(Enum):
    MUST_FIT = "must_fit"       # truncated only as a last resort
    OPTIONAL = "optional"       # dropped entirely if budget exhausted

@dataclass(frozen=True)
class ContextItem:
    text: str                   # rendered into the section body
    embed_key: str              # stable cache key for this item's embedding

@dataclass(frozen=True)
class Section:
    name: str                   # header label, e.g. "ACTIVE FILE", "PRIOR VIOLATIONS"
    items: list[str | ContextItem]
    priority: Priority
    soft_budget: int            # tokens this section wants
    retriever: Retriever | None = None
    truncate: Literal["head", "tail"] = "tail"

async def assemble(
    sections: list[Section],
    *,
    total_budget: int,
    counter: TokenCounter,
    query: str | None = None,
) -> str:
    ...
```

### Algorithm

1. Sum `soft_budget` of all `MUST_FIT` sections as `reserved`. If `reserved >
   total_budget`, proportionally scale each must-fit section's effective
   budget so the sum fits, and truncate each section's rendered body at its
   own `truncate` direction (`"head"` or `"tail"`). Log a warning with the
   numbers (indicates a recipe bug).
2. `remaining = total_budget − reserved`.
3. Walk `OPTIONAL` sections in listed order (recipe authors control priority
   within the tier by list position):
   - If the section has a `retriever` and `query is not None`, rank items via
     `await retriever.rank(items, query)`. Otherwise keep original order.
   - Fill items (in ranked order) until the section's `soft_budget` **or**
     `remaining` is hit. Drop tail items that don't fit.
   - If zero items fit, drop the section entirely — no empty headers in output.
4. Render each surviving section as `f"{name}:\n{item_1}\n{item_2}\n..."`,
   separated by blank lines. Return the joined string.

### Retriever protocol

```python
class Retriever(Protocol):
    async def rank(self, items: list[ContextItem], query: str) -> list[ContextItem]:
        ...
```

Two implementations ship v1:

- `NullRetriever` — returns items unchanged. Used for string-only sections
  and as the graceful-degradation fallback.
- `SemanticRetriever(embedder: OllamaEmbedder)` — cosine-ranks items against
  the query embedding using cached vectors.

---

## Embedding infrastructure

`embeddings.py` — single responsibility, async, cache-backed.

```python
class OllamaEmbedder:
    def __init__(
        self,
        host: str,                            # from SentinelConfig.ollama.host
        model: str = "nomic-embed-text",
        cache: Cache | None = None,
        client: httpx.AsyncClient | None = None,
    ): ...

    async def embed(self, text: str, *, cache_key: str | None = None) -> list[float]:
        """
        POST {host}/api/embeddings with {"model": model, "prompt": text}.
        Cache vectors under f"embed:{model}:{cache_key}" if cache_key is given.
        Raise EmbeddingUnavailable on timeout, network error, or model-not-pulled.
        """
```

**Cache schema.** Namespace `embed:{model}:{cache_key}` → `list[float]`. Model
name is part of the key; swapping the embedding model is a cache wipe without
any manual intervention. Entries are content-addressed by `cache_key`, so they
never expire. Callers provide stable keys (e.g. `finding:{id}`,
`query:{sha256(query)}`).

**Cosine similarity.** Computed in pure Python with `math.sqrt` — ~5 lines.
Avoids a numpy dependency. Acceptable for expected working-set sizes.

### SemanticRetriever.rank()

1. Embed the query once (cached under `query:{sha256(query)}`).
2. Gather embeddings for all items in parallel (`asyncio.gather`), each using
   its own `embed_key`.
3. Cosine-score items against the query, return highest-first.
4. If any `embed` call raises `EmbeddingUnavailable`, abort ranking, log once,
   return items in original order.

---

## ViolationDB migration

Additive, backward-compatible. No rewrite.

### Schema change

```sql
ALTER TABLE findings ADD COLUMN embed_text TEXT;
```

Vectors live in `Cache`, not in SQLite. Keyed by `finding:{id}`. Rationale:
swapping the embedding model is a cache invalidation, not a schema migration.

### Migration path

`ViolationDB.__init__` now runs a `_migrate()` step after `CREATE TABLE IF NOT
EXISTS`:

1. Inspect `PRAGMA table_info(findings)`.
2. If `embed_text` column missing, add it.
3. Run one `UPDATE` to synthesize `embed_text` for existing rows:
   `f"[{severity}] {category} at {file_path}:{line_start}: {description}"`.
4. Idempotent — safe to call on every startup.

### New methods

```python
def get_all_unresolved(self) -> list[dict]: ...
    # Every unresolved finding across all files, with embed_text populated.

async def get_neighbors_by_similarity(
    self, query_text: str, embedder: OllamaEmbedder, k: int = 10,
) -> list[dict]: ...
    # Embed query_text, score every unresolved finding's cached embedding,
    # return top-k. Convenience for the sentinel recipe.
```

`get_unresolved(file_path)` stays available as the lower-level exact-path
read. The recipe chooses the semantic path by default.

### Populating embed_text

In `persist_findings()`:

- **Insert path:** set `embed_text` at insert.
- **Upsert path:** leave `embed_text` alone (text didn't change; description
  is part of the uniqueness key).

### Config addition

```python
class MemoryConfig(BaseModel):
    enabled: bool = True
    db_path: str = ".ollama_reviews/memory.db"
    neighbor_k: int = 10              # NEW
    semantic_recall: bool = True      # NEW — feature flag
```

When `semantic_recall=False`, the sentinel recipe falls back to the old
`get_unresolved(file_path)` exact-match query.

---

## Recipes

Two named orchestrators in `recipes.py`. They encode each module's section
list, budgets, and retriever wiring. Consumers call one function.

### `build_review_context` (sentinel)

Replaces the body of `FileProcessor.format_prompt`.

```python
async def build_review_context(
    *,
    file_rel_path: str,
    file_type: str,
    content: str | None,
    diff: str | None,
    chunk_info: str,                    # "" or " (Part 2/5)"
    prior_violations: list[dict],       # caller fetches (async boundary)
    counter: TokenCounter,
    total_budget: int,
    retriever: Retriever,               # SemanticRetriever or NullRetriever
) -> str:
    sections = [
        Section(
            name=f"FILE: {file_rel_path}{chunk_info}",
            items=[_render_file_block(content, diff, file_type)],
            priority=Priority.MUST_FIT,
            soft_budget=int(total_budget * 0.70),
            truncate="tail",
        ),
    ]
    if prior_violations:
        sections.append(Section(
            name="PRIOR UNRESOLVED ISSUES (address or escalate if still present)",
            items=[
                ContextItem(text=_render_violation(v), embed_key=f"finding:{v['id']}")
                for v in prior_violations
            ],
            priority=Priority.OPTIONAL,
            soft_budget=int(total_budget * 0.25),
            retriever=retriever,
        ))
    return await assemble(
        sections, total_budget=total_budget, counter=counter, query=content or diff,
    )
```

### `build_research_context` (research agent)

Replaces the 4000-char truncation in `SynthesisTool._preprocess_sources` and
the manual concatenation of `code_context`, `impact_analysis`, and
`web_sources`.

```python
async def build_research_context(
    *,
    query: str,
    web_sources: list[ContentItem],
    code_results: str | None,
    impact: ImpactAnalysis | None,
    counter: TokenCounter,
    total_budget: int,
    retriever: Retriever,
) -> str:
    sections = []
    if impact and impact.items:
        sections.append(Section(
            name="IMPACT ANALYSIS",
            items=[format_impact_report(impact)],
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

---

## Integration points

### Sentinel (`ollama_sentinel/processor.py`)

- `format_prompt()` becomes a thin async wrapper that calls
  `build_review_context(...)`. Signature changes from sync to async.
  `generate_review()` already awaits it.
- `_get_prior_violations()` is renamed to `_get_ranked_prior_violations()`.
  When `config.memory.semantic_recall=True`, it calls
  `violation_db.get_neighbors_by_similarity(query_text=file_content,
  embedder=embedder, k=config.memory.neighbor_k)`. Otherwise it keeps the
  existing exact-path `get_unresolved(rel)` call.
- `chunk_content_by_lines` (currently in `utils.py`) is moved into
  `context/assembler.py` as a helper that counts in tokens, not chars.
  `FileProcessor.chunk_content` now chunks by token budget, not char budget.

### Research agent (`research_agent/tools/synthesis.py`)

- `SynthesisTool._preprocess_sources` (the 4000-char truncator) is deleted.
- The handlebars `{{#each web_sources}}` loop in the synthesis template is
  replaced by a single `{{assembled_context}}` placeholder.
- `SynthesisTool.synthesize(...)` keeps its current signature (`query`,
  `sources`, `code_context`, `impact_analysis`) but its body now calls
  `build_research_context(query=..., web_sources=sources,
  code_results=code_context, impact=impact_analysis, counter=self.counter,
  total_budget=self.total_budget, retriever=self.retriever)` and passes the
  returned string into the template as a single `{{assembled_context}}`
  variable. Callers in `workflow.py` are unchanged.
- `EnhancedMemoryStore.find_similar_webpages/queries` are updated to use
  `SemanticRetriever` under the hood. The token-overlap fallback is kept
  behind the `EmbeddingUnavailable` catch.

### Config (`ollama_sentinel/models.py`)

New and modified fields:

```python
class OllamaModelConfig(BaseModel):
    # ... existing fields
    context_window: int = 8192           # NEW — model's token context window
    output_reserve_tokens: int = 2000    # NEW — leave room for the response

class EmbeddingConfig(BaseModel):        # NEW
    enabled: bool = True
    model: str = "nomic-embed-text"

class MemoryConfig(BaseModel):
    enabled: bool = True
    db_path: str = ".ollama_reviews/memory.db"
    neighbor_k: int = 10                 # NEW
    semantic_recall: bool = True         # NEW

class SentinelConfig(BaseModel):
    # ... existing fields
    embedding: EmbeddingConfig = EmbeddingConfig()  # NEW
```

`ProcessingConfig.max_chars_per_chunk` and `overlap_chars` are retired; chunk
sizing now derives from `ollama.models["default"].context_window -
output_reserve_tokens`.

Research-agent TOML gains `api.synthesis_context_tokens` (default `12000`).

---

## Error handling

Every failure degrades gracefully. Review output is never blocked by
embedding infrastructure.

| Failure | Behavior |
|---|---|
| Ollama `/api/embeddings` timeout or 404 (embedding model not pulled) | `OllamaEmbedder.embed` raises `EmbeddingUnavailable`. Retrievers catch, log once per assembly, return items in original order. |
| `tiktoken` import / encoding failure | `TokenCounter` falls back to `len(text) // 3.5` estimator. Logs once at startup. |
| `sum(soft_budget for MUST_FIT) > total_budget` | Proportional truncation of must-fit sections. `log.warning` with the numbers (recipe bug signal). |
| Single item's token count exceeds its section's budget | Truncate item at the section's `truncate` direction; append `"… [truncated]"`. Never drop a must-fit section's only item silently. |
| `ViolationDB._migrate()` cannot add `embed_text` column (read-only FS, corrupt DB) | Log error, force `memory.semantic_recall = False` for the session. |
| `OPTIONAL` section has zero items after ranking | Section dropped entirely — no empty `"PRIOR UNRESOLVED ISSUES:"` header. |
| Any retriever exception other than `EmbeddingUnavailable` | Logged at error level, retriever falls back to identity ordering. Assembly continues. |

---

## Testing

Pytest, `asyncio_mode = "auto"`. `pytest-httpx` for all Ollama calls. No live
Ollama required for any test. Class-based organization, fixtures in
`tests/conftest.py`.

### New test files

1. `tests/context/test_assembler.py` — pure-function tests with
   `FakeTokenCounter` (`count = len`) and `FakeRetriever` (reverses order).
   Covers: must-fit always renders, optional drops when budget exhausted,
   proportional must-fit truncation, empty-optional dropped, retriever
   ordering applied, `truncate="head"` vs `"tail"`, single-item overflow.
2. `tests/context/test_tokens.py` — `tiktoken` integration happy path;
   fallback estimator when tiktoken is unavailable.
3. `tests/context/test_embeddings.py` — `pytest-httpx` mocks for
   `/api/embeddings`. Covers: cache hit skips HTTP, cache miss populates,
   model-name-in-key invalidation, `EmbeddingUnavailable` on timeout and on
   HTTP 404.
4. `tests/context/test_retrievers.py` — `SemanticRetriever` cosine ordering
   with a fake embedder returning hand-crafted vectors; degradation path
   when embedder raises `EmbeddingUnavailable`.
5. `tests/context/test_recipes.py` — integration of `build_review_context`
   and `build_research_context` with a fake embedder. Asserts exact rendered
   output shape and budget discipline.

### Extended tests

6. `tests/test_violation_db.py` — add `test_migrate_adds_embed_text_column`,
   `test_migrate_is_idempotent`, `test_get_neighbors_by_similarity`,
   `test_semantic_fallback_when_column_missing`.
7. `tests/test_processor.py` — update existing tests to expect
   `format_prompt` to be async and to call the recipe. Fixture injects a
   dummy `ContextBuilder` for the legacy assertions.

### Targets

- ~30–40 new tests.
- Full suite stays under the current ~2-second budget (all HTTP mocked).
- Coverage gate unchanged.

---

## Decided side-points

- `EmbeddingConfig.host` is deliberately absent — host is reused from
  `OllamaConfig.host`. If a user later wants embeddings on a different
  Ollama instance, an optional override can be added then. Not in v1.
- `ProcessingConfig.max_chars_per_chunk` and `overlap_chars` removal is
  paired with a deprecation-warning path: if either field is present in a
  user's YAML, `ProcessingConfig` logs a one-time warning and ignores the
  value. Lands in the same change.

## Not in scope for this spec

- `ollama-sentinel triage <stdin>` (terminal triage command). Separate idea
  from the original brainstorm; out of scope here.
- Symbol-aware chunking via `ast`/tree-sitter. Tracked as a follow-up after
  token-aware chunking lands.
- A VSCode extension wrapping ollama-sentinel. Phind's UI patterns are not
  being replicated in an editor; they're being ported into the Python stack.
