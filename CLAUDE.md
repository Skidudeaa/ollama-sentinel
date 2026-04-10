# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Ollama Sentinel is an automated code review system with two independent modules:

1. **ollama_sentinel** — Watches a directory for file changes and sends modified files to a local Ollama instance for AI-powered code review. Reviews are saved as versioned markdown/JSON/HTML files.
2. **research_agent** — A multi-step research agent using LangGraph that orchestrates web search, browser scraping, code indexing, synthesis, and verification through an `analyze → search → read → code_search → synthesize → verify → (refine loop)` workflow.

These two modules are architecturally independent. The sentinel uses Ollama (local models via httpx), while the research agent uses OpenAI via LangChain.

## Build & Run Commands

```bash
# Install (editable mode)
pip install -e .

# Run the sentinel watcher
ollama-sentinel run                          # uses ollama-sentinel.yaml
ollama-sentinel run -c path/to/config.yaml   # custom config
ollama-sentinel run -v                       # verbose logging

# Review a single file
ollama-sentinel review path/to/file.py
ollama-sentinel review path/to/file.py -m security   # use a specific model role

# Initialize a new config file
ollama-sentinel init [directory]

# Research agent (Click CLI, run as module)
python -m research_agent.main query "your question" --context file.py --output result.md
python -m research_agent.main interactive
python -m research_agent.main setup
```

**Prerequisites**: A running Ollama instance at `http://localhost:11434` (configurable in YAML). The research agent additionally requires `OPENAI_API_KEY` and optionally `SERPAPI_API_KEY` environment variables.

## Testing

No test suite exists yet. The `.gitignore` includes pytest patterns, so use pytest as the framework when adding tests.

## Architecture

### ollama_sentinel data flow

```
CLI (typer) → FileSentinel → awatch loop with adaptive debounce
                                ↓
                          FileProcessor.generate_review()
                            ├─ prepare_file_content() (full read or git diff)
                            ├─ chunk_content_by_lines() (line-aware, with overlap)
                            └─ OllamaClient.generate_review() (httpx POST /api/chat, tenacity retry)
                                ↓
                          FileProcessor.save_review()
                            └─ versioned output with history cleanup
```

- **Concurrency control**: Semaphore limits concurrent reviews (`max_concurrent_reviews`) and concurrent chunks per file (`max_concurrent_chunks_per_file`).
- **Ignore logic**: Uses `pathspec` (gitwildmatch) combining config patterns + `.gitignore`.
- **Security**: `safe_read()` blocks symlinks and path traversal outside watch directory.

### research_agent data flow

```
Click CLI → ResearchAgent → build_workflow() → LangGraph StateGraph
  analyze → search (SERPAPI/DDG) → read (Playwright browser) →
  code_search (LlamaIndex) → synthesize (ChatOpenAI) →
  verify → conditional: finalize | refine → search loop
```

- **Config**: Singleton `Config` class with TOML file + env var overrides + dot-notation access (`config.get("api.openai_model")`).
- **State**: `AgentState` TypedDict flows through the graph carrying session, results, answer, and confidence.
- **Router**: `verify_router` decides finalize vs. refine based on `verification.verified` and iteration count.

### Duplicate top-level directories

`cli/` and `core/` at the repo root appear to be duplicates/earlier versions of `research_agent/cli/` and `research_agent/core/`. The canonical code lives inside the `research_agent/` package.

## Configuration

**ollama_sentinel**: YAML config validated by Pydantic models in `ollama_sentinel/models.py`. The `OllamaConfig` validator requires a `"default"` model key. See `ollama-sentinel.yaml` for the full schema.

**research_agent**: TOML config with defaults in `research_agent/core/config.py`. Key env overrides: `OPENAI_API_KEY`, `SERPAPI_API_KEY`, `RESEARCH_MODEL`, `RESEARCH_USE_LOCAL_EMBEDDINGS`, `RESEARCH_CACHE_PATH`, `RESEARCH_DB_PATH`.

## Key Conventions

- Async-first I/O throughout (httpx, watchfiles, Playwright). The research agent's synchronous LangGraph nodes wrap async calls with `asyncio.new_event_loop()`.
- Pydantic v2 for config validation in ollama_sentinel; raw dicts with singleton Config in research_agent.
- File-based storage: reviews go to `.ollama_reviews/` mirroring source structure with timestamped versions.
- Retry with exponential backoff via tenacity on Ollama API calls.
- The CLI entry point is registered as `ollama-sentinel` via `[project.scripts]` in pyproject.toml, backed by `ollama_sentinel.cli:app` (Typer). The research agent uses a separate Click CLI.
