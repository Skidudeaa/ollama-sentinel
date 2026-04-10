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
pytest tests/ -v                    # 232 tests, <1 second
pytest tests/ -k "security"         # run security-specific tests
pytest tests/test_violation_db.py   # run one module's tests
```

**Test conventions**: pytest with `asyncio_mode = "auto"`. Use `tmp_path` for filesystem tests. Use `pytest-httpx` (`httpx_mock`) for HTTP mocking. Class-based test organization. Fixtures in `tests/conftest.py`.

## Architecture

### Sentinel data flow (with violation memory)

```
CLI (Typer) -> FileSentinel -> awatch loop (debounce)
                                 |
                           FileProcessor.generate_review()
                             |- prepare_file_content() via asyncio.to_thread
                             |- ViolationDB.get_unresolved() -> prior violations
                             |- format_prompt() with PRIOR UNRESOLVED ISSUES block
                             |- chunk_content_by_lines() (line-aware, overlap)
                             |- OllamaClient.generate_review() (httpx, tenacity retry)
                                 |
                           extract_findings() (LLM JSON + regex fallback)
                             |- ViolationDB.persist_findings() (SQLite upsert)
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
| `ollama_sentinel/processor.py` | FileProcessor, OllamaClient, prompt formatting, review generation |
| `ollama_sentinel/violation_db.py` | SQLite-backed Finding persistence with upsert and occurrence counting |
| `ollama_sentinel/extractor.py` | LLM JSON extraction + regex fallback for parsing review findings |
| `ollama_sentinel/watcher.py` | FileSentinel, file watching, ignore logic, pipeline orchestration |
| `ollama_sentinel/models.py` | Pydantic v2 config models with validators (host, output dir, memory) |
| `ollama_sentinel/cli.py` | Typer CLI: run, review, init, report |
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
- `EnhancedMemoryStore.find_similar_*` uses token-overlap scoring, not embeddings. Good enough for keyword matching but won't find semantic similarity.
- The `config/` directory at repo root contains unrelated codex artifacts (sqlite3, secret_key) -- should be gitignored or removed
- `"ollama_sentinel copy/"`, `"config copy/"`, `"reviews copy"` are stale duplicates that should be deleted
- Stale duplicate directories `cli/` and `core/` at root were deleted but the copy variants remain
