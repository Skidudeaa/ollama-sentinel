# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

Ollama Sentinel is a local-first AI development companion with two independent modules:

1. **ollama_sentinel** -- File watcher that sends code to a local Ollama model for review, with cumulative violation memory that learns your codebase's recurring issues over time.
2. **research_agent** -- Multi-step research agent using LangGraph that produces dependency-aware impact analysis when researching library migrations, CVEs, or API changes.

The two modules are architecturally independent. The sentinel uses Ollama (local models via httpx). The research agent uses OpenAI via LangChain.

## Build & Run

```bash
pip install -e .                    # sentinel only
pip install -e ".[research]"        # + research agent deps
pip install -e ".[dev]"             # + pytest/testing deps

ollama-sentinel run                 # watch directory, auto-review
ollama-sentinel review file.py      # review a single file
ollama-sentinel review file.py -m security   # use security model role
ollama-sentinel report              # show recurring violations
ollama-sentinel init                # create config file

python -m research_agent.main query "question" --context file.py --output result.md
python -m research_agent.main interactive
python -m research_agent.main setup
```

**Prerequisites**: Ollama at `http://localhost:11434`. Research agent needs `OPENAI_API_KEY` and optionally `SERPAPI_API_KEY`.

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v                    # ~278 tests, <2 seconds
pytest tests/ -k "security"         # run security-specific tests
pytest tests/test_violation_db.py   # run one module's tests
```

**Test conventions**: pytest with `asyncio_mode = "auto"`. Use `tmp_path` for filesystem tests. Use `pytest-httpx` (`httpx_mock`) for HTTP mocking. Class-based test organization. Fixtures in `tests/conftest.py`.

## Architecture

### Sentinel data flow (with violation memory + semantic recall)

```
CLI (Typer) -> FileSentinel -> awatch loop (debounce)
                                 |
                           FileProcessor.generate_review()
                             |- prepare_file_content() via asyncio.to_thread
                             |- _get_ranked_prior_violations()
                             |    semantic: ViolationDB.get_neighbors_by_similarity()
                             |    fallback:  ViolationDB.get_unresolved(path)
                             |- format_prompt() -> build_review_context() recipe
                             |    MUST_FIT: active file / diff block
                             |    OPTIONAL: PRIOR UNRESOLVED (retriever-ranked)
                             |- chunk_content() -> chunk_by_lines (token-aware)
                             |- OllamaClient.generate_review() (httpx, tenacity retry)
                                 |
                           extract_findings() (LLM JSON + regex fallback)
                             |- ViolationDB.persist_findings() (SQLite upsert,
                                populates embed_text for semantic recall)
                                 |
                           FileProcessor.save_review()
                             |- versioned output with history cleanup
```

### Research agent data flow (with impact analysis)

```
Click CLI -> ResearchAgent -> LangGraph StateGraph
  analyze -> search -> read -> code_search -> impact_scan -> synthesize -> verify
                                                  |                          |
                                        ImportResolver (AST)         verify_router
                                        Entity extraction          /            \
                                        Call site matching    finalize        refine -> search
                                        Severity classification
                                        ImpactAnalysis (structured)
```

### Key modules

| Module | Purpose |
|--------|---------|
| `ollama_sentinel/processor.py` | FileProcessor, OllamaClient, async prompt formatting via recipe, review generation |
| `ollama_sentinel/violation_db.py` | SQLite-backed Finding persistence with upsert, `embed_text` column, and `get_neighbors_by_similarity` |
| `ollama_sentinel/extractor.py` | LLM JSON extraction + regex fallback for parsing review findings |
| `ollama_sentinel/watcher.py` | FileSentinel, file watching, ignore logic, pipeline orchestration |
| `ollama_sentinel/models.py` | Pydantic v2 config models: Ollama/Embedding/Memory/Processing with validators |
| `ollama_sentinel/cli.py` | Typer CLI: run, review, init, report |
| `ollama_sentinel/context/assembler.py` | `Section` / `Priority` / `ContextItem` dataclasses + `assemble()` + `chunk_by_lines` — pure, token-budgeted |
| `ollama_sentinel/context/tokens.py` | `TokenCounter` (tiktoken `cl100k_base` with char-based fallback) |
| `ollama_sentinel/context/embeddings.py` | `OllamaEmbedder` — async `/api/embeddings` client, cache-backed, `EmbeddingUnavailable` on failure |
| `ollama_sentinel/context/retrievers.py` | `NullRetriever`, `SemanticRetriever` (cosine, pure Python) |
| `ollama_sentinel/context/recipes.py` | `build_review_context`, `build_research_context` — named recipes consumed by sentinel and research agent |
| `research_agent/core/workflow.py` | LangGraph StateGraph with all nodes including impact_scan |
| `research_agent/tools/import_resolver.py` | AST-based Python import graph resolver |
| `research_agent/tools/synthesis.py` | Answer synthesis with structured impact report output |
| `research_agent/tools/memory.py` | Cache-backed persistent memory store |
| `research_agent/utils/cache.py` | JSON-serialized diskcache (no pickle) |

### Security boundaries

- `safe_read()` uses `Path.relative_to()` for containment (not string prefix)
- `OllamaConfig` validates host URL scheme (http/https only)
- `OutputConfig` rejects `..` traversal and absolute paths
- `BrowserTool._validate_url()` blocks private IPs and non-http schemes
- `Cache` uses JSON serialization (no pickle deserialization attacks)

## Configuration

**Sentinel** (YAML, validated by Pydantic):
- `ollama-sentinel.yaml` -- see file for full schema
- `OllamaConfig` requires a `"default"` model key
- `MemoryConfig` controls violation memory (`enabled`, `db_path`)

**Research agent** (TOML + env vars):
- Singleton `Config` with `Config.reset()` for test isolation
- Key env vars: `OPENAI_API_KEY`, `SERPAPI_API_KEY`, `RESEARCH_MODEL`

## Key Conventions

- Python >= 3.10
- Async-first I/O: httpx, watchfiles, Playwright. Blocking calls wrapped with `asyncio.to_thread()`
- Pydantic v2 API: `@field_validator`, `.model_dump()` (not v1 `@validator`/`.dict()`)
- Type hints throughout
- SQLite with WAL mode for concurrent access (ViolationDB)
- Best-effort extraction: finding extraction and violation persistence never block review saving
- All new features have tests before merge

## Known Issues / Next Session Breadcrumbs

- Research agent requires `pip install -e ".[research]"` (heavy deps: langchain, playwright, llama-index). Not installed by default.
- `impact_scan` node tested with mocked logic only -- needs integration test against real LangGraph compile with OpenAI key
- `ollama-sentinel run` requires `ollama pull nomic-embed-text` once on first use (or set `memory.semantic_recall: false` to fall back to the legacy exact-path recall).
- `EnhancedMemoryStore.find_similar_*` (research agent) still uses token-overlap scoring. `ViolationDB` now has real semantic recall via `get_neighbors_by_similarity`; the `EnhancedMemoryStore` upgrade is deferred as optional Phase 9 follow-up of the ContextBuilder plan.
- `_archive/` holds superseded snapshots (`ollama_sentinel_pre_memory_snapshot/`, `research_agent_orphans/`). Do not import from it. See `_archive/README.md` for provenance.
- 2026-04-16: ContextBuilder landed (plan: `docs/superpowers/plans/2026-04-16-context-builder.md`). Prompt assembly + violation memory are now embedding-ranked and token-budgeted. Tests: 278 passed, 15 skipped, ~1.4s.
- ContextBuilder follow-ups (deferred, not blockers): (1) `_format_impact_report` in `ollama_sentinel/context/recipes.py` diverges from `SynthesisTool.format_impact_report` (missing `SUGGESTED FIRST COMMIT` block) — dedupe when a second caller appears. (2) Add a `SemanticRetriever`-through-`build_review_context` integration test to `tests/context/test_recipes.py`. (3) Phase 9 optional: upgrade `EnhancedMemoryStore.find_similar_*` to use `SemanticRetriever`.
