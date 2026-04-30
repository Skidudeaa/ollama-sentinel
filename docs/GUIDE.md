# Ollama Sentinel -- User Guide

## What This Is

Most AI code review tools see your code once -- when you open a PR. Then they forget everything.

Ollama Sentinel is different. It watches every save. It remembers every finding. After a week, it knows that you keep forgetting to close database connections in `repositories/`. After a month, it knows that nullable returns in your auth flow have been flagged five times and never fixed. It tells your Ollama model about these patterns, and the model escalates them.

The research agent does the same thing for library migrations. Instead of a generic essay about "what changed in SQLAlchemy 2.0," it scans your actual codebase, finds the 14 call sites that break, ranks them by severity, and tells you which file to fix first.

Both tools are local. Your code never leaves your machine. The memory they build is yours.

---

## Getting Started

### 1. Install

```bash
git clone https://github.com/skidudeaa/ollama-sentinel.git
cd ollama-sentinel
pip install -e .
```

### 2. Start Ollama

You need a running Ollama instance. If you don't have one:

```bash
# Install Ollama (macOS)
brew install ollama

# Pull a model
ollama pull gemma3:4b

# Start the server (runs on localhost:11434)
ollama serve
```

### 3. Initialize

```bash
ollama-sentinel init
```

This creates `ollama-sentinel.yaml` in your current directory. The defaults work out of the box with `gemma3:4b`.

### 4. Run

```bash
ollama-sentinel run
```

Now edit any file in the directory. Save it. Within a few seconds, a review appears in your terminal and is saved to `.ollama_reviews/`.

---

## The Sentinel

### How It Works

1. You save a file
2. The watcher detects the change (with debounce to avoid reviewing mid-keystroke)
3. The file content is read (or git diff, if configured)
4. If violation memory is enabled, prior unresolved findings for this file are fetched from the SQLite database and injected into the prompt
5. The content + prior findings are sent to your Ollama model
6. The model returns a review
7. Findings are extracted from the review (LLM JSON parsing with regex fallback) and persisted to the database
8. The review is saved as a versioned markdown file

Every review makes the next one smarter.

### Commands

```bash
# Watch and auto-review
ollama-sentinel run
ollama-sentinel run -v              # verbose logging
ollama-sentinel run -c my.yaml      # custom config

# Review one file manually
ollama-sentinel review src/auth.py
ollama-sentinel review src/auth.py -m security    # use security-focused model

# See what keeps breaking
ollama-sentinel report
ollama-sentinel report -n 3         # only show issues seen 3+ times
ollama-sentinel report -f json      # machine-readable output
ollama-sentinel report -l 50        # show up to 50 violations

# Create a config file
ollama-sentinel init
ollama-sentinel init ./my-project -o ./reviews

# Diagnose tool output (pytest, mypy, traceback, etc.) with auto-extracted source context
ollama-sentinel triage < pytest.log
ollama-sentinel triage error.log -o triage.md
ollama-sentinel triage error.log --no-extract        # skip source extraction
ollama-sentinel triage error.log --context src/foo.py:42  # add explicit context

# Live TUI of recent reviews + recurring violations (read-only, polls the DB)
ollama-sentinel dashboard
ollama-sentinel dashboard -r 0.5 -n 3                # half-second refresh, min count 3
```

### The Report

After running reviews for a while, `ollama-sentinel report` shows you the patterns:

```
             Recurring Violations (seen >= 2x)
+------+-------+----------+----------+--------------+-------+---------------------+
| #    | Count | Severity | Category | File         | Lines | Description         |
+------+-------+----------+----------+--------------+-------+---------------------+
| 1    | 7     | high     | bug      | src/db.py    | 42-45 | Connection not      |
|      |       |          |          |              |       | closed in finally   |
| 2    | 5     | critical | security | src/auth.py  | 88-92 | SQL injection via   |
|      |       |          |          |              |       | string format       |
| 3    | 3     | medium   | design   | src/api.py   | 15-30 | God function, split |
|      |       |          |          |              |       | into smaller units  |
+------+-------+----------+----------+--------------+-------+---------------------+
```

This is the view you bring to a sprint planning meeting.

### Configuration

Edit `ollama-sentinel.yaml`:

```yaml
watch:
  directory: "."               # what to watch
  recursive: true              # include subdirectories
  ignore_patterns:             # gitignore-style patterns
    - "*.md"
    - "*.log"
    - "**/.git/**"
    - "**/node_modules/**"
    - "**/__pycache__/**"
  debounce_ms: 1500            # wait this long after last change

ollama:
  host: "http://localhost:11434"
  models:
    default:                   # every config needs a "default" model
      name: "gemma3:4b"
      system_prompt: >
        You are a senior code reviewer. Identify bugs with line numbers,
        design smells, and small refactors. Respond in markdown.
      temperature: 0.1
    security:                  # optional: specialized model roles
      name: "gemma3:4b"
      system_prompt: >
        You are a security auditor. Focus on injection, auth bypass,
        data exposure, and OWASP Top 10 vulnerabilities.
      temperature: 0.1

processing:
  max_chars_per_chunk: 12000   # split large files at this threshold
  overlap_chars: 500           # overlap between chunks for context
  max_concurrent_reviews: 3    # parallel file reviews
  max_concurrent_chunks_per_file: 2
  git_diff_mode: false         # true = review only the diff, not full file

output:
  directory: ".ollama_reviews" # where reviews are saved
  format: "markdown"           # markdown, json, or html
  console_output: true         # print to terminal too
  compress: false              # gzip old versions
  history:
    enabled: true
    max_versions: 5            # keep last N versions per file

memory:
  enabled: true                # cumulative violation memory
  db_path: ".ollama_reviews/memory.db"
```

### Multiple Model Roles

You can define specialized models for different review focuses:

```bash
ollama-sentinel review src/payment.py -m security
```

The `-m` flag selects which model role to use. Define as many as you need in the YAML under `ollama.models`.

---

## The Research Agent

### What It Does

You ask a question about a library change. Instead of a generic answer, you get an impact analysis of YOUR codebase:

```bash
python -m research_agent.main query "sqlalchemy 2.0 migration" \
  --context src/db/ --output migration-plan.md
```

Output:

```
IMPACT ANALYSIS: 14 call sites across 6 files

HIGH SEVERITY (breaking):
  src/db/session.py:47     Session.execute(text_query) -> Use session.execute(text(...))
  src/db/session.py:83     engine.execute() removed -> Use with engine.connect() as conn
  src/models/user.py:112   Query.get() removed -> Use Session.get(User, id)

MEDIUM SEVERITY (deprecated):
  src/api/handlers.py:201  relationship(lazy="dynamic") -> Use write_only

SUGGESTED FIRST COMMIT:
  [ ] src/db/session.py:47 - Wrap text queries with text()
  [ ] src/db/session.py:83 - Replace engine.execute with connection context
  [ ] src/models/user.py:112 - Replace Query.get with Session.get
```

### How It Works

1. **Analyze** -- LLM plans the research approach
2. **Search** -- Web search (SerpAPI or DuckDuckGo) for relevant sources
3. **Read** -- Playwright browser fetches and extracts content from top results
4. **Code search** -- LlamaIndex vector search over your repo
5. **Impact scan** -- AST-based import graph resolution + entity matching against your actual code
6. **Synthesize** -- Produces structured impact report (or narrative if no code impact found)
7. **Verify** -- LLM checks the answer for accuracy; loops back to refine if needed

Results are cached. The second time you ask about the same library, unchanged files are skipped.

### Installation

The research agent has heavier dependencies:

```bash
pip install -e ".[research]"

# Playwright needs browser binaries
playwright install chromium
```

### Environment

```bash
export OPENAI_API_KEY="sk-..."          # required
export SERPAPI_API_KEY="..."            # optional (falls back to DuckDuckGo)
export RESEARCH_MODEL="gpt-4o"         # optional (default: gpt-4o-preview)
```

### Commands

```bash
# One-shot query
python -m research_agent.main query "how to migrate from Flask to FastAPI" \
  --context ./src --output migration.md

# Interactive session
python -m research_agent.main interactive

# Check your environment is set up
python -m research_agent.main setup
```

---

## The Philosophy

Every cloud AI tool forgets you the moment the conversation ends. Copilot Review sees your PR once. CodeRabbit sees your PR once. ChatGPT sees whatever you paste.

Ollama Sentinel accumulates. It watches every save, not just PRs. It remembers every finding, not just the current review. It builds a violation database that is specific to YOUR codebase, YOUR team's patterns, YOUR recurring blind spots.

The research agent does the same for migrations. It doesn't just explain what changed in a library -- it tells you exactly where your code breaks, ranked by severity, with a suggested first commit.

The structural advantage is locality and persistence. A tool that lives on your machine, watches your work continuously, and builds memory that compounds over weeks is fundamentally different from a tool you visit when you remember to.

The switching cost is real. After three months, the violation database knows things about your codebase that don't exist anywhere else. That's the point.

---

## Project Structure

```
ollama-sentinel/
  ollama_sentinel/           # sentinel module
    cli.py                   # Typer CLI (run, review, init, report, triage, dashboard)
    config.py                # YAML config loading
    models.py                # Pydantic v2 config models
    processor.py             # FileProcessor, OllamaClient, async prompt formatting
    watcher.py               # FileSentinel, file watching, pipeline orchestration
    violation_db.py          # SQLite violation memory + semantic recall (embed_text)
    extractor.py             # Finding extraction (LLM + regex fallback)
    dashboard.py             # Live TUI (Rich) for reviews + recurring violations
    utils.py                 # safe_read, chunking, diff, compression
    context/                 # token-budgeted prompt assembly + semantic retrieval
      assembler.py           # Section / Priority / ContextItem + assemble + chunk_by_lines
      tokens.py              # TokenCounter (tiktoken cl100k_base, char fallback)
      embeddings.py          # OllamaEmbedder (async /api/embeddings, cache-backed)
      retrievers.py          # NullRetriever, SemanticRetriever (cosine, pure Python)
      recipes.py             # build_review_context / build_research_context / build_triage_context
    triage/                  # `ollama-sentinel triage` pipeline
      extractor.py           # regex extraction of file+line refs (traceback/pytest/mypy/ruff)
      runner.py              # run_triage() + TRIAGE_SYSTEM_PROMPT, hybrid role fallback

  research_agent/            # research agent module (separate stack: OpenAI + LangChain)
    main.py                  # Click CLI (query, interactive, setup)
    core/
      workflow.py            # LangGraph StateGraph with all nodes (incl. impact_scan)
      agent.py               # ResearchAgent orchestrator
      models.py              # Data models (ContentItem, ImpactItem, etc.)
      config.py              # Singleton TOML config
      logging.py             # Module logger setup
    tools/
      import_resolver.py     # AST-based Python import graph
      browser.py             # Playwright web scraping
      search.py              # Web search (SerpAPI/DuckDuckGo)
      synthesis.py           # Answer synthesis + impact report formatting
      verification.py        # Answer verification
      memory.py              # Cache-backed persistent memory
      code_context.py        # LlamaIndex vector search over the user's repo
    utils/
      cache.py               # JSON-serialized diskcache (no pickle)
      extraction.py          # HTML content extraction
      embedding.py           # Vector embedding helpers
      setup.py               # Initialization / env validation
    cli/
      history.py             # Interactive REPL history
      interface.py           # Interactive REPL UI

  tests/                     # 336 tests, ~3 seconds
  docs/
    plans/                   # implementation plans
    superpowers/             # specs, plans, and follow-ups for landed features
    GUIDE.md                 # this file
  _archive/                  # superseded snapshots (do not import; see _archive/README.md)
  ollama-sentinel.yaml       # example config
  pyproject.toml             # package config
```
