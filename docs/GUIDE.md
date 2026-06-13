# Ollama Sentinel -- User Guide

> **▶ Prefer the visual walkthrough?** Open [`index.html`](index.html) in a browser
> for the same content as a single-page infographic — hero, architecture diagram,
> command grid, sample outputs, dashboard sketch, annotated config.

## What This Is

Most AI code review tools see your code once -- when you open a PR. Then they forget everything.

Ollama Sentinel is different. It watches every save. It remembers every finding. After a week, it knows that you keep forgetting to close database connections in `repositories/`. After a month, it knows that nullable returns in your auth flow have been flagged five times and never fixed. It tells your Ollama model about these patterns, and the model escalates them.

And once a pattern is confirmed often enough, it hardens into a **guardrail** — a named rule the reviewer checks on every future change. You can author guardrails by hand, or let the sentinel propose them from shapes it has flagged and corroborated three or more times. That's the compounding payoff: the codebase's failure history stops being a log you read and becomes a rulebook the model enforces.

The research agent does the same thing for library migrations. Instead of a generic essay about "what changed in SQLAlchemy 2.0," it scans your actual codebase, finds the 14 call sites that break, ranks them by severity, and tells you which file to fix first.

Both tools are local. Your code never leaves your machine. The memory they build is yours.

---

## Getting Started

### 1. Install

```bash
git clone https://github.com/Skidudeaa/ollama-sentinel.git
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

## The Control Center

The Control Center is the primary product surface. It shows everything the sentinel knows at a glance.

```bash
ollama-sentinel                    # opens the Control Center (default behavior)
ollama-sentinel dashboard          # same thing, explicit command
ollama-sentinel dashboard -r 0.5   # half-second refresh rate
```

### What You See

The Control Center is a full-screen read-only TUI with three main areas:

**Overview** (top-left) — aggregate system state:
- Open findings count with severity breakdown (critical/high/medium/low)
- New findings in the last 7 days
- The "hottest" file (most unresolved findings)
- A suggested next action based on current state

**Recent Reviews** (bottom-left) — the latest review output files with relative timestamps, showing what was reviewed and when.

**Patterns** (right) — recurring violations ranked by frequency. These are the issues the model keeps flagging across multiple reviews. If something shows up here repeatedly, it needs real attention.

**Guardrails** (right, below Recent Reviews) — your active project rules, with their scope and source (✎ authored by hand, `auto` promoted from a pattern). Read-only here; curate them with the `guardrail` commands below.

### Status Indicators

The header shows:
- **Watcher status** — Active (review <60s ago), Idle (<5m), or Stale (>5m)
- **DB status** — total open findings count, or "no DB yet" if reviews haven't run
- **Model** — which Ollama model is configured
- **Update time** — when the display last refreshed

The Control Center polls every 1 second by default. It doesn't modify any state — it's purely a read-only view of what the watcher has produced.

### Keyboard Navigation

The Control Center is interactive. When running in a terminal (TTY), these keys are available:

| Key | Action |
|-----|--------|
| `q` | Quit |
| `Tab` | Cycle focus to next panel (Overview → Reviews → Patterns) |
| `Shift-Tab` | Cycle focus to previous panel |
| `j` / `↓` | Move selection down in the focused list |
| `k` / `↑` | Move selection up in the focused list |
| `Enter` | Open detail view for the selected item |
| `Esc` | Close detail view or cancel filter |
| `/` | Enter filter mode (filters Patterns by severity or category) |

**Filter mode:** Type a severity (`high`, `crit`) or category (`security`, `perf`) to narrow the Patterns list. Press Enter to lock the filter, Esc to cancel. All keys (including `q`, `j`, `k`) type normally while filtering.

**Detail mode:** Press Enter on a review or pattern to see full details in a dedicated panel. Press Esc to return to the normal view.

The focused panel has a bright cyan border. The selected row is highlighted with reverse video.

---

## The Sentinel

### How It Works

1. You save a file
2. The watcher detects the change (with debounce to avoid reviewing mid-keystroke)
3. The file content is read (or git diff, if configured)
4. If violation memory is enabled, prior unresolved findings for this file are fetched from the SQLite database
5. Active guardrails whose scope matches the file are loaded and (with the prior findings) injected into the prompt — relevance-ranked and token-budgeted
6. The content + guardrails + prior findings are sent to your Ollama model
7. The model returns a review
8. Findings are extracted (LLM JSON parsing with regex fallback), best-effort tagged with the guardrail that produced them, and persisted to the database
9. The review is saved as a versioned markdown file

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

# Control Center (live TUI, read-only, polls the DB)
ollama-sentinel                                      # default: opens Control Center
ollama-sentinel dashboard                            # same thing, explicit command
ollama-sentinel dashboard -r 0.5 -n 3                # half-second refresh, min count 3

# Findings lifecycle
ollama-sentinel findings                             # list open findings with ids (--severity / --file / -f json)
ollama-sentinel resolve 42                           # close as fixed
ollama-sentinel dismiss 31                           # close as false-positive
ollama-sentinel fix 42                               # localized model fix → preview diff → write on confirm (--yes)
ollama-sentinel surface                              # emit open findings to .ollama_reviews/findings.sarif (editor + CI)
ollama-sentinel prune                                # close findings whose flagged code is gone

# Incidents (corroborated events)
ollama-sentinel confirm 42                           # manual corroboration → manual_confirm Incident (finding stays open)
ollama-sentinel incidents                            # list corroborated events (table / -f json)
ollama-sentinel install-hooks                        # git post-commit hook: link commits to open findings

# Guardrails (project rules — see the section below)
ollama-sentinel guardrail add no-eval -a "Never eval untrusted input." --category security --path "src/*.py"
ollama-sentinel guardrail list                       # active rules (--all / --status / -f json)
ollama-sentinel guardrail edit 1 --assertion "..."   # disable / enable / dismiss <id> manage lifecycle
ollama-sentinel guardrail candidates                 # auto-detected recurring shapes (on-demand)
ollama-sentinel guardrail promote 1                  # confirm candidate → active guardrail
ollama-sentinel guardrail reject 1                   # suppress a candidate shape
```

### The Report

After running reviews for a while, `ollama-sentinel report` shows you the patterns:

```
                    Patterns (seen >= 2x)
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

## Project Guardrails

A **guardrail** is a named, natural-language rule the reviewer checks explicitly on every relevant change — `"Never call eval/exec on untrusted input."`, `"Database sessions must be closed in a finally block."`, `"Don't log request bodies."` Where prior-violation memory reminds the model what it *has* flagged, a guardrail tells it what to *always* check. It's the codebase's hard-won lessons, made durable and enforced.

Guardrails are stored in the same memory DB (no YAML), so they survive restarts and authoring one needs neither Ollama nor a prior review.

### Two ways a guardrail is born

```
   You author one directly  ─────────────────────────────╮
   (guardrail add)                                        ▼
                                                   active guardrail ──► injected into every
   ≥3 distinct corroborated findings of one shape         ▲            matching review
     ─► guardrail candidates (on-demand clustering)       │
        ─► LLM-drafted assertion                          │
        ─► guardrail promote  (you confirm) ──────────────╯
        ─► guardrail reject   (suppress this shape)
```

Both paths converge on one active artifact. Manual authoring gives value on day one with zero history; auto-promotion compounds on top once a shape has recurred enough to be trustworthy.

### Authoring and lifecycle

```bash
# Create — active immediately. Scope is optional; omit it to apply broadly.
ollama-sentinel guardrail add no-eval \
  -a "Never call eval/exec on untrusted input." \
  --category security \        # scope to one finding category (optional)
  --path "src/*.py"            # scope to a path glob (optional)

ollama-sentinel guardrail list            # active rules
ollama-sentinel guardrail list --all      # include disabled/dismissed
ollama-sentinel guardrail list -f json    # machine-readable

ollama-sentinel guardrail edit 1 --assertion "..." --category bug   # change any field
ollama-sentinel guardrail disable 1       # stop injecting it (reversible)
ollama-sentinel guardrail enable 1        # bring it back
ollama-sentinel guardrail dismiss 1       # terminal — never injected again
```

A guardrail's **scope** decides which files it applies to. A `--path` glob is matched segment-precisely against the file under review (`src/*.py` admits `src/app.py`, not `src/sub/app.py`); an absent glob applies broadly. The `--category` is carried for attribution and clustering — it doesn't restrict which files the rule appears in (a file has no category until it's reviewed).

### How guardrails shape a review

When you `review` or `run`, the reviewer loads every **active** guardrail whose scope matches the file, ranks them by relevance to the file's contents, and injects the top ones into the prompt under a `PROJECT GUARDRAILS — check explicitly` heading — capped by a token budget so a growing rulebook never floods the prompt. Disabled and dismissed guardrails are never injected. When the model then flags something, that finding is best-effort tagged with the guardrail that produced it (its *provenance*), which feeds the auto-promotion integrity gate below.

### Auto-promotion: from pattern to rule

Authoring is the day-one path; the compounding layer is promotion. Run on demand:

```bash
ollama-sentinel guardrail candidates       # detect recurring shapes (table or -f json)
```

This selects findings that have been **corroborated** (≥1 Incident — a test failure, a fix commit, or a manual confirmation), groups them by category, and clusters them by semantic similarity. A shape with **three or more distinct corroborated findings** becomes a *candidate*, each presented with a one-line assertion drafted by your local model (with a deterministic fallback if the model is unavailable). You then decide:

```bash
ollama-sentinel guardrail promote 1        # confirm candidate #1 → active guardrail (source=promoted)
ollama-sentinel guardrail promote 1 --assertion "..." --name my-rule   # edit at confirm time
ollama-sentinel guardrail reject 1         # not a real rule → suppress this shape from future runs
```

Two safeguards keep this honest:

- **Nothing enforces without your confirm.** A candidate is a proposal; only `promote` turns it into an injected rule. `reject` records the shape so it isn't re-proposed.
- **A rule can't manufacture its own evidence.** A guardrail flags findings (tagged with its provenance). Those findings could, in principle, cluster back into a candidate that re-proposes the *same* rule — a self-reinforcing echo. The **evidence-integrity gate** blocks that: a guardrail's own findings count toward a candidate only when corroborated by a *hard* signal (a `test_failure` or `fix_commit` Incident), never by a bare `manual_confirm` or an uncorroborated opinion. Independently-discovered findings always count.

**Prerequisites for candidates:** the embedding model (`ollama pull qwen3-embedding:4b`) and `embedding.enabled` in your config, plus real incident history. Clustering runs *only* in the `candidates`/`promote`/`reject` commands — never on the watcher or dashboard loop — so it never taxes live review latency. On a fresh DB there's nothing to detect yet; author guardrails by hand and let candidates accrue.

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
# One-shot query (unified CLI — preferred)
ollama-sentinel research "how to migrate from Flask to FastAPI" \
  --context ./src/app.py --output migration.md

# Interactive session
ollama-sentinel research -i

# Legacy entry point (still works)
python -m research_agent.main query "same question" --context ./src --output result.md
python -m research_agent.main interactive
python -m research_agent.main setup
```

Research results are automatically persisted and visible in the Control Center's Overview panel.

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
    cli.py                   # Typer CLI (run, review, init, report, triage, dashboard,
                             #   findings lifecycle, incidents, guardrail sub-app)
    config.py                # YAML config loading
    models.py                # Pydantic v2 config models
    processor.py             # FileProcessor, OllamaClient, async prompt formatting,
                             #   guardrail loading + provenance attribution
    watcher.py               # FileSentinel, file watching, pipeline orchestration
    violation_db.py          # SQLite memory: findings + incidents + guardrails tables,
                             #   semantic recall (embed_text), corroborated-findings selector
    guardrails.py            # Guardrail shape clustering, candidate detection,
                             #   assertion drafting, evidence-integrity gate
    extractor.py             # Finding extraction (LLM + regex fallback)
    dashboard.py             # Live TUI (Rich): reviews + recurring violations + guardrails
    sarif.py                 # SARIF surface (`surface`) + stale-prune selector (`prune`)
    remediate.py             # Localized model fix for `fix <id>` (excerpt-bounded, atomic)
    hooks.py                 # Git post-commit hook installer + record_commit
    pytest_plugin.py         # Opt-in plugin: test failure → test_failure Incident
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

  tests/                     # run `pytest tests/ -q` for the live count (~10s)
  docs/
    plans/                   # implementation plans
    superpowers/             # specs, plans, and follow-ups for landed features
    VISION.md                # product vision + roadmap
    index.html               # single-page visual guide (canonical pitch surface)
    GUIDE.md                 # this file
  _archive/                  # superseded snapshots (do not import; see _archive/README.md)
  ollama-sentinel.yaml       # example config
  pyproject.toml             # package config
```
