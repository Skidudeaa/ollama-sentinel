# Ollama Sentinel

> **▶ Visual guide:** open [`docs/index.html`](docs/index.html) in a browser for the full walkthrough — `open docs/index.html` on macOS, or `xdg-open docs/index.html` on Linux.

A local AI development companion that remembers.

**The sentinel** watches your code directory, reviews every change with a local Ollama model, and builds a cumulative violation database that tracks recurring issues across your codebase. After a week it knows your blind spots. After a month it's indispensable.

**The research agent** produces dependency-aware impact analysis for library migrations, CVEs, and API changes -- not generic essays, but ranked lists of every call site in YOUR code that breaks, with severity and suggested fixes.

Both tools are local. Your code never leaves your machine.

## Quick start

### First-time setup (do this once)

```bash
pip install -e .                        # install the sentinel
ollama pull gemma3:4b                   # the reviewer model (~3 GB)
ollama pull qwen3-embedding:4b          # the semantic-recall embedder (~2.5 GB)
cd <your-project-dir>
ollama-sentinel init                    # writes ollama-sentinel.yaml in cwd
```

If you'd rather use a cloud model (e.g. `deepseek-v4-pro:cloud`) instead of `gemma3:4b`, skip the first `ollama pull`, run `ollama signin` once, and edit `ollama-sentinel.yaml` so `ollama.models.default.name` is your cloud model.

### Each time you want to use it

You need **two terminals**:

```bash
# Terminal 1 — directory doesn't matter
ollama serve

# Terminal 2 — cwd MUST be the directory containing ollama-sentinel.yaml
cd <your-project-dir>
ollama-sentinel run
```

That's it. You do **not** need `ollama run <model>` — the sentinel hits Ollama's HTTP API directly and the model lazy-loads on first request.

Edit any file in the watched directory and a markdown review lands in `.ollama_reviews/<filename>.md` within a few seconds.

**Two things to look for so you know it's working:**

- The watcher terminal prints `Watching <dir> for changes` on startup
- After you save a file, it prints `Persisted N findings for <filename>` and `Saved review to .ollama_reviews/<filename>_<timestamp>.md`

To stop: `Ctrl+C` in the watcher terminal.

### The Control Center

Open a third terminal (or use the watcher terminal after stopping it) to see what the sentinel has learned:

```bash
ollama-sentinel              # launches the Control Center (same as 'dashboard')
```

The Control Center shows:
- **Overview** — open findings, severity breakdown, hottest file, suggested next action
- **Recent Reviews** — latest review output with timestamps
- **Patterns** — recurring violations ranked by frequency (the issues that keep coming back)

This is the primary product surface. Everything the sentinel knows is visible here at a glance.

> **Heads up — directory matters.** `ollama-sentinel run` reads `ollama-sentinel.yaml` from the **current working directory**. If you have stale YAMLs in multiple project folders, the cwd one wins. Run from anywhere with `ollama-sentinel run --config <abs-path-to-ollama-sentinel.yaml>` if that's a problem.

## Documentation

See **[docs/GUIDE.md](docs/GUIDE.md)** for the full user guide covering:
- Sentinel setup, commands, and configuration
- Violation memory and the `report` command
- Research agent installation and usage
- Impact analysis output format
- Project philosophy and architecture

## Cheat sheet

The "I always forget what to run" table.

| I want to... | Run | What success looks like |
|---|---|---|
| Open the Control Center | `ollama-sentinel` | Full-screen TUI with overview, reviews, and patterns |
| Start the watcher | `ollama-sentinel init && ollama-sentinel run` | "Watching `<dir>` for changes". Edit a file → review lands in `.ollama_reviews/<name>.md` within seconds |
| Review one file | `ollama-sentinel review src/foo.py` | Markdown review prints to stdout AND lands in `.ollama_reviews/foo.py.md` |
| Use a model role | `ollama-sentinel review src/foo.py -m security` | Review references security concerns specifically (vs the default reviewer's broader feedback) |
| See recurring violations | `ollama-sentinel report` | Rich table ranked by occurrence count |
| Same, machine-readable | `ollama-sentinel report -f json` | JSON array on stdout |
| Diagnose a failing log | `ollama-sentinel triage < pytest.log`<br>or `ollama-sentinel triage some.log -o out.md` | Markdown diagnosis with file:line references |
| Create a config file | `ollama-sentinel init` | Writes `ollama-sentinel.yaml` in the current dir |
| Research a question | `ollama-sentinel research "is SQLAlchemy 2.0 safe to upgrade?"` | Synthesized answer with confidence score, persisted to Control Center |
| Research with code context | `ollama-sentinel research "what breaks?" --context src/db.py` | Answer grounded in your actual code |
| Interactive research | `ollama-sentinel research -i` | REPL prompt — ask follow-up questions in the same session |
| Run all tests | `pytest tests/ -q` | `413 passed, 15 skipped` (the 15 skips are intentional — fallback paths covered by the other CI runner) |

## When something looks wrong

| Symptom | Fix |
|---|---|
| Watcher stalls, no reviews appear | Ollama isn't running. `ollama serve` in another terminal |
| `404 Not Found` from `/api/chat` or `/api/embeddings` | The model name in your YAML isn't pulled (or for `:cloud` models, you're not signed in). Check `ollama list` against `ollama.models.default.name` and `embedding.models.hot` in the loaded YAML. Pull the missing model or run `ollama signin` |
| Sentinel uses the wrong model / config | You have multiple `ollama-sentinel.yaml` files. The one in cwd wins. Either `cd` to the right directory or pass `--config <abs-path-to-ollama-sentinel.yaml>` |
| `EmbeddingUnavailable` in logs | `ollama pull qwen3-embedding:4b` (or set `memory.semantic_recall: false` in the YAML) |
| `EmbeddingUnavailable` only on the first review after Ollama restart | Cold-load timeout. Bump `embedding.timeout_seconds` in the YAML (default 30s, sized against ~6.4s realistic idle cold-load on M-series) |
| `ValidationError: extra fields not permitted` on config load | YAML typo — error names the offending field; fix the spelling. Applies to top-level fields AND role names inside `embedding.models` |
| `ValidationError: ... must include a 'hot' role` | YAML's `embedding:` block is missing `models.hot`. Add `embedding: { models: { hot: qwen3-embedding:4b } }` |
| Deprecation warning about `embedding.model` on every load | Legacy v0.1.x flat shape. Migrate `embedding.model: foo` → `embedding.models.hot: foo`. The legacy field hard-errors in v0.3 |
| Research agent ImportError | `pip install -e ".[research]"` (the `[research]` extras are not installed by default) |

## Requirements

- Python 3.10+
- [Ollama](https://ollama.ai) running locally (sentinel)
- `OPENAI_API_KEY` env var (research agent only)
- `pip install -e ".[research]"` for research agent dependencies

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -q   # 378 passed, 15 skipped, ~2-3 seconds
```

CI runs both `[dev]` and `[dev,research]` matrix runners on every push and PR — the second one exercises the 15 tests skipped on `[dev]`-only.

## License

MIT
