# Ollama Sentinel

> ### ⏸ Status: parked (2026-08-25)
>
> This ran for real. From 2026-08-08 it watched **six live projects across two
> machines** for 17 days and produced **18,864 findings**. Then it was shut
> down, deliberately, and the project was parked.
>
> **What the trial showed:** the reviewer works. It found a zero-width space
> (U+200B) hiding inside a regex, an XSS cluster, a hardcoded API-key fallback,
> and a silent-data-loss race — the kind of defect that survives a human read.
> The cumulative memory worked too: reviews came back annotated
> `STILL PRESENT (flagged 4× since ...)`.
>
> **What it also showed:** ~1,100 findings a day is more than any one person
> triages, and no amount of polish fixes that ratio. Continuous passive review
> only pays if you read the review when it lands.
>
> - [The verdict, with measurements](docs/session-notes/2026-08-25-bake-verdict-and-fleet-shutdown.md)
> - [The defects it found, triaged](docs/harvest/bake-harvest-2026-08-25.md)
>
> The code works and the test suite is green — nothing here is abandoned or
> broken. It is simply not being developed, and nothing is running. If you want
> the capability, read the verdict first; it says honestly which parts earned
> their keep.

> **▶ Visual guide:** open [`docs/index.html`](docs/index.html) in a browser for the full walkthrough — `open docs/index.html` on macOS, or `xdg-open docs/index.html` on Linux.

A local AI development companion that remembers.

**The sentinel** watches your code directory, reviews every change with a local Ollama model, and builds a cumulative violation database that tracks recurring issues across your codebase. Recurring issues resurface in later reviews instead of being rediscovered from scratch. It also turns recurring confirmed failures into **guardrails** — named rules the reviewer checks on every future change, either authored by you or auto-promoted from a shape it has flagged and corroborated three or more times.

**The research agent** produces dependency-aware impact analysis for library migrations, CVEs, and API changes -- not generic essays, but ranked lists of every call site in YOUR code that breaks, with severity and suggested fixes.

Both tools are local. Your code never leaves your machine.

## Quick start

### First-time setup (do this once)

```bash
pip install -e .                        # install the sentinel
ollama pull qwen3-embedding:4b          # the semantic-recall embedder (~2.5 GB)
cd <your-project-dir>
ollama-sentinel init                    # writes ollama-sentinel.yaml in cwd
```

`init` queries `/api/tags` and picks a reviewer model you already have installed, so you usually don't need to pull one. If nothing suitable is installed it falls back to `gemma3:4b` — `ollama pull gemma3:4b` (~3 GB) covers that case.

To use a cloud model (e.g. `deepseek-v4-pro:cloud`), run `ollama signin` once and edit `ollama-sentinel.yaml` so `ollama.models.default.name` is your cloud model. **Note:** every `:cloud` model ignores Ollama's `format` JSON schema, so grounded extraction degrades to the regex path (`extract_findings_legacy`), which synthesizes `verbatim_excerpt` directly from the cited source lines so `fix`, `prune`, and `surface` still work. Set `processing.grounding: false` to skip the schema attempt and see the [verdict note](docs/session-notes/2026-08-25-bake-verdict-and-fleet-shutdown.md) for background.

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

You can also triage and fix findings directly inside the Control Center: select a pattern (or press `Enter` for Detail mode) and press `r` to resolve, `d` to dismiss, `c` to corroborate as an Incident, or `f` to generate an excerpt-bounded fix preview (`y` to apply, `n`/`Esc` to cancel).

This is the primary product surface. Everything the sentinel knows is visible and actionable here at a glance.

> **Heads up — directory matters.** `ollama-sentinel run` reads `ollama-sentinel.yaml` from the **current working directory**. If you have stale YAMLs in multiple project folders, the cwd one wins. Run from anywhere with `ollama-sentinel run --config <abs-path-to-ollama-sentinel.yaml>` if that's a problem.

> **Hot-reload (POSIX).** Edit the YAML while the watcher is running and send `kill -HUP <pid>` — the sentinel reloads model and `request_timeout` settings in place without dropping the watch. (Changing `watch.directory` still needs a restart; it's warned-and-skipped on reload.)

## Project guardrails

A **guardrail** is a named, natural-language rule the reviewer checks explicitly on every relevant change — your codebase's hard-won lessons, made durable. Guardrails live in the same memory DB (no YAML), so authoring one works on a fresh setup with no Ollama running:

```bash
ollama-sentinel guardrail add no-eval \
  -a "Never call eval/exec on untrusted input." \
  --category security --path "src/*.py"     # scope is optional
ollama-sentinel guardrail list              # active rules (--all for disabled/dismissed)
```

From then on, when you `review`/`run`, each active guardrail whose scope matches the file under review is injected into the prompt (relevance-ranked, token-budgeted), and any finding it produces is tagged with that guardrail's provenance. Disable, re-enable, edit, or dismiss a guardrail at any time:

```bash
ollama-sentinel guardrail edit 1 --assertion "..."   # change name / assertion / scope
ollama-sentinel guardrail disable 1   #  / enable 1  / dismiss 1
```

**Auto-promotion.** Guardrails also grow on their own. Once a shape has been flagged and **corroborated three or more times** (distinct findings, each with a test failure / fix-commit / confirmation), it surfaces as a *candidate* with an LLM-drafted assertion you edit and confirm:

```bash
ollama-sentinel guardrail candidates   # needs the embedder; on-demand only
ollama-sentinel guardrail promote 1    # confirm candidate #1 → active guardrail
ollama-sentinel guardrail reject 1     # not a rule → suppress this shape
```

Nothing becomes an enforced rule without your explicit `promote`, and a guardrail's own flagged findings only reinforce a candidate via a *hard* signal (a test failure or a fix commit, never a bare model opinion) — so a rule can't manufacture its own re-promotion. This is the **Finding → Incident → Pattern** loop: model opinion → corroborated event → durable project rule.

## Documentation

See **[docs/GUIDE.md](docs/GUIDE.md)** for the full user guide covering:
- Sentinel setup, commands, and configuration
- Violation memory and the `report` command
- Project guardrails — authoring, lifecycle, and auto-promotion
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
| Review git-changed or staged files | `ollama-sentinel check`<br>or `ollama-sentinel check --staged --fail-on high` | Reviews modified/staged files concurrently, prints a findings table, and exits `1` when findings meet `--fail-on` |
| Use a model role | `ollama-sentinel review src/foo.py -m security` | Review references security concerns specifically (vs the default reviewer's broader feedback) |
| See recurring violations | `ollama-sentinel report` | Rich table ranked by occurrence count |
| Same, machine-readable | `ollama-sentinel report -f json` | JSON array on stdout |
| Diagnose a failing log | `ollama-sentinel triage < pytest.log`<br>or `ollama-sentinel triage some.log -o out.md` | Markdown diagnosis with file:line references |
| Corroborate a finding | `ollama-sentinel confirm 42` | Records a `manual_confirm` Incident; the Finding stays open |
| See corroborated events | `ollama-sentinel incidents`<br>or `ollama-sentinel incidents -f json` | Incidents (test failures, confirmations, fix commits) as a table or JSON |
| Surface findings in your editor | `ollama-sentinel surface` | Writes `.ollama_reviews/findings.sarif`; open it in VS Code/Cursor (SARIF Viewer → Problems panel) or upload it in CI for GitHub code scanning. Findings are re-anchored to current lines by excerpt; `run` refreshes it automatically |
| List open findings | `ollama-sentinel findings`<br>or `… --severity high --file foo.py` | Table with ids, ranked by severity then frequency; `-f json` for machine-readable |
| Close a finding | `ollama-sentinel resolve 42` / `ollama-sentinel dismiss 31` | `resolve` = fixed, `dismiss` = false-positive; records why so the dismiss rate is a usable signal |
| Apply a localized fix | `ollama-sentinel fix 42` / `… --yes` | Asks the local model for a fix to that finding's exact span, previews a unified diff, and on confirmation writes it into the file and resolves the finding (`fixed`). Never writes without an interactive yes or `--yes`; no commit |
| Prune stale findings | `ollama-sentinel prune` / `… --yes` | Previews the open findings whose flagged code is gone (file deleted or excerpt no longer locatable) and, on confirmation, closes them with `resolution='stale'`. Read-only on source; no Incident; still-locatable findings are left open |
| Repair historical findings | `ollama-sentinel repair` / `… --yes` | Re-evaluates stored descriptions with code spans/negated severity stripped and backfills missing `verbatim_excerpt` from disk |
| Link commits / gate pre-commit | `ollama-sentinel install-hooks`<br>or `ollama-sentinel install-hooks --pre-commit` | Installs the `post-commit` hook (links commits to open Findings) and optionally a `pre-commit` hook (`check --staged --fail-on high`) |
| Auto-link test failures | add `ollama_sentinel = true` to your pytest config | A failing test on a flagged line becomes a `test_failure` Incident |
| Author a guardrail | `ollama-sentinel guardrail add no-eval -a "Never eval untrusted input." --category security --path "src/*.py"` | A named rule the reviewer checks on matching files; active immediately, no Ollama needed to create it |
| List / curate guardrails | `ollama-sentinel guardrail list`<br>`… edit/disable/enable/dismiss <id>` | `list` shows active rules (`--all`, `-f json`); the others manage the lifecycle |
| See auto-detected candidates | `ollama-sentinel guardrail candidates` | Recurring shapes (≥3 corroborated findings) with an LLM-drafted assertion; on-demand, needs the embedder |
| Promote / reject a candidate | `ollama-sentinel guardrail promote 1` / `… reject 1` | `promote` confirms it into an active guardrail (`source=promoted`); `reject` suppresses that shape |
| Create a config file | `ollama-sentinel init` | Writes `ollama-sentinel.yaml` in the current dir |
| Research a question | `ollama-sentinel research "is SQLAlchemy 2.0 safe to upgrade?"` | Synthesized answer with confidence score, persisted to Control Center |
| Research with code context | `ollama-sentinel research "what breaks?" --context src/db.py` | Answer grounded in your actual code |
| Interactive research | `ollama-sentinel research -i` | REPL prompt — ask follow-up questions in the same session |
| Run all tests | `pytest tests/ -q` | Green (run it for the live count — it drifts as tests land; the skips are intentional, exercised by the `[dev,research]` CI runner) |

## Known limits & resolved trial defects

Found by running this against six real projects for 17 days (see [CLAUDE.md](CLAUDE.md) for details):

| Item | Status | Effect |
|---|---|---|
| **Severity keyword-scanned over quoted code** (`extractor.py`) | **Fixed** | Fenced/inline code spans and negated phrases (`not critical`) are now stripped before keyword scanning, and explicit `Severity:`/`Category:` labels take precedence. |
| **Regex extraction path missing `verbatim_excerpt`** (`extractor.py`, `violation_db.py`) | **Fixed** | `extract_findings_legacy` now synthesizes `verbatim_excerpt` from the cited source line span (and `persist_findings` backfills empty excerpts on upsert), so `fix`, `prune`, and `surface` work with `:cloud` models. |
| **Stale findings lingering after edits** (`watcher.py`, `sarif.py`) | **Fixed** | Saving a file now runs `reconcile_file_findings` before review generation, auto-closing vanished findings (`resolution='stale'`) and updating shifted line spans in place. |
| **Findings accumulate faster than anyone triages them in passive mode** | **Structural** | Six projects produced ~1,100/day under continuous watching. Scope `watch.directory` tightly, use `ollama-sentinel check --staged` for pre-commit gating, or triage directly in the Control Center (`r`/`d`/`c`/`f`). |

## When something looks wrong

| Symptom | Fix |
|---|---|
| Watcher stalls, no reviews appear | Ollama isn't running. `ollama serve` in another terminal |
| `404 Not Found` from `/api/chat` or `/api/embeddings` | The model name in your YAML isn't pulled (or for `:cloud` models, you're not signed in). Check `ollama list` against `ollama.models.default.name` and `embedding.models.hot` in the loaded YAML. Pull the missing model or run `ollama signin` |
| `/api/chat` `ReadTimeout` after a long wait | The review model is taking longer than `ollama.request_timeout`. For thinking cloud models, set `think: false` under the model role; for any slow model, lower `max_tokens` or use a faster local model |
| Sentinel uses the wrong model / config | You have multiple `ollama-sentinel.yaml` files. The one in cwd wins. Either `cd` to the right directory or pass `--config <abs-path-to-ollama-sentinel.yaml>` |
| `EmbeddingUnavailable` in logs | `ollama pull qwen3-embedding:4b` (or set `memory.semantic_recall: false` in the YAML) |
| `guardrail candidates` shows nothing | Auto-promotion needs ≥3 *distinct corroborated* findings of one shape, plus the embedder. On a fresh DB there's no history yet — author guardrails by hand (`guardrail add`) and let candidates accrue. Requires `embedding.enabled` and `ollama pull qwen3-embedding:4b` |
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
pytest tests/ -q   # green in ~10s; run it for the live pass/skip count (it drifts as tests land)
```

CI runs both `[dev]` and `[dev,research]` matrix runners on every push and PR — the second one exercises the tests skipped on `[dev]`-only (the research-extras suite).

## License

MIT
