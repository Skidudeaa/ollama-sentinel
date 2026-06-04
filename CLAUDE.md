# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

> **▶ Visual guide first:** The single-file infographic at [`docs/index.html`](docs/index.html)
> is the canonical walkthrough. Open it in a browser before reading further —
> it's where the architecture, philosophy, sample outputs, and data flow live
> as a coherent narrative. Do **not** delete or move it without explicit
> instruction. It's also linked from `README.md`, the v0.1.0 GitHub Release,
> and `docs/GUIDE.md`.

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
ollama-sentinel triage < pytest.log # diagnose tool output via local model
ollama-sentinel triage log.txt -o out.md   # triage a saved log, save result
ollama-sentinel dashboard           # live TUI: recent reviews + recurring violations
ollama-sentinel confirm 42          # manually corroborate a Finding -> Incident
ollama-sentinel incidents           # list corroborated events (table or -f json)
ollama-sentinel install-hooks       # install the git post-commit hook
ollama-sentinel record-commit       # link HEAD to open Findings (called by the hook)
ollama-sentinel surface             # emit open Findings to .ollama_reviews/findings.sarif (editor Problems panel + CI)
ollama-sentinel findings            # list open Findings with ids (filter: --severity/--file)
ollama-sentinel resolve 42          # close Finding 42 as fixed (resolution='fixed')
ollama-sentinel dismiss 31          # close Finding 31 as false-positive (resolution='dismissed')

python -m research_agent.main query "question" --context file.py --output result.md
python -m research_agent.main interactive
python -m research_agent.main setup
```

**Prerequisites**: Ollama at `http://localhost:11434`. Research agent needs `OPENAI_API_KEY` and optionally `SERPAPI_API_KEY`.

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v                    # full suite (~10s); run `pytest tests/ -q` for the live pass/skip count
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

### Incident corroboration (v0.2: Finding -> Incident)

Findings are model opinions; Incidents are objective events that corroborate
them. Three independent paths promote an open Finding into an Incident — none
auto-creates a Finding (no matching Finding -> no Incident):

```
open Finding (from the review path above)
   |
   |- pytest_plugin: test fails on file:line within +/-tolerance of a Finding
   |     -> persist_incident(confirming_signal="test_failure", node id as artifact)
   |- hooks.record_commit (post-commit): commit touches a flagged file
   |     -> link_commit_to_findings() sets triggering_commit_sha
   |- cli.confirm <finding_id>: manual corroboration
   |     -> persist_incident(confirming_signal="manual_confirm")
   |- ViolationDB.mark_resolved(*, fix_commit=): a fix lands
   |     -> persist_incident(confirming_signal="fix_commit")
   v
incidents table  -- inspect with `ollama-sentinel incidents` (table / JSON)
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
| `ollama_sentinel/cli.py` | Typer CLI: run, review, init, report, triage, dashboard, confirm, incidents, install-hooks, record-commit, surface, findings, resolve, dismiss |
| `ollama_sentinel/pytest_plugin.py` | Opt-in pytest plugin: matches test failures to open Findings, records `test_failure` Incidents (`pytest11` entry point) |
| `ollama_sentinel/hooks.py` | Git post-commit hook installer + `record_commit` (links commits to open Findings) |
| `ollama_sentinel/dashboard.py` | Live Rich TUI for `ollama-sentinel dashboard` — polls reviews dir + ViolationDB read-only |
| `ollama_sentinel/sarif.py` | SARIF 2.1.0 surface: excerpt-based `relocate_finding`, `build_sarif` document, `generate_sarif_file` (read-only orchestration) — backs the `surface` command + watcher auto-refresh |
| `ollama_sentinel/context/assembler.py` | `Section` / `Priority` / `ContextItem` dataclasses + `assemble()` + `chunk_by_lines` — pure, token-budgeted |
| `ollama_sentinel/context/tokens.py` | `TokenCounter` (tiktoken `cl100k_base` with char-based fallback) |
| `ollama_sentinel/context/embeddings.py` | `OllamaEmbedder` — async `/api/embeddings` client, cache-backed, `EmbeddingUnavailable` on failure |
| `ollama_sentinel/context/retrievers.py` | `NullRetriever`, `SemanticRetriever` (cosine, pure Python) |
| `ollama_sentinel/context/recipes.py` | `build_review_context`, `build_research_context`, `build_triage_context` — named recipes consumed by sentinel and research agent |
| `ollama_sentinel/triage/extractor.py` | Pure regex-driven extraction of file+line references from tool output (traceback/pytest/mypy/ruff/generic) |
| `ollama_sentinel/triage/prompts.py` | `TRIAGE_SYSTEM_PROMPT` — leaf module, no intra-package imports |
| `ollama_sentinel/triage/runner.py` | `run_triage()` — orchestrates extract → recipe → model with hybrid role fallback |
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

### Repo state as of 2026-06-04 (last session)

- **v0.1.1 shipped**; repo public at <https://github.com/Skidudeaa/ollama-sentinel>.
- **Test suite:** run `pytest tests/ -q` for the live count (this session it was
  620 passed / 15 skipped, ~10s). Do **not** hardcode the number here again — it
  drifts every time tests land. Quote the command, not the count.
- **The "make findings actionable" arc** is the live thread: surface → triage →
  remediate → stale-prune. Slices 1-2 (surface #14, triage #15) are merged.
  Slice 3 (remediate `fix <id>`) and slice 4 (stale-prune `prune`) have specs;
  remediate implementation is underway (see Recent landings).
- **Working tree should be clean.** If it isn't, `git status` first.
- **The visual guide (`docs/index.html`) is the canonical pitch surface.**
  Linked from README, GUIDE.md, and the v0.1.0 release notes.

### Resume here next time

1. **Sanity check.** `pytest tests/ -q` should be green.
2. **Finish the remediate arc.** Spec (revised for safety):
   `docs/superpowers/specs/2026-06-03-remediate-fix-design.md`. It builds as a
   4-piece stack: `0` relocation-exactness (sarif) → `1` write-primitives
   (utils: `read_strict`/`safe_write`) → `2` `remediate.py` core → `3` `fix` CLI.
   This is the first code path that writes into watched source — the spec's
   safety properties (strict-UTF8 read, mode preservation, exact-span-only,
   TOCTOU re-read) are load-bearing; keep them.
3. **Then stale-prune.** Spec: `docs/superpowers/specs/2026-06-04-stale-prune-design.md`
   (slice 4, spec-only so far).

### Pickable next moves (ordered by leverage)

| # | Item | Effort | Risk | Notes |
|---|---|---|---|---|
| 1 | Build remediate `fix <id>` (4-piece stack, per revised spec) | M | med | First write-to-source path; spec carries the safety design. |
| 2 | Build stale-prune `prune` (per spec) | S-M | low | Closes the stranded-stale-finding leak; read-only on source. |
| 3 | OP-1 — SIGHUP hot-reload of `ollama-sentinel.yaml` (`docs/superpowers/followups.md`) | M | med | Real DX pain on long-running watchers. |
| 4 | CB-1 — dedupe impact-report formatter (`recipes.py` vs `synthesis.py`) | ~30-45 min | low | Dormant; only triggers if `build_research_context` gets impact data. |

Skip TR-3 — deliberate spec deviation, documented in followups.md. Qwen3
Phases B/C stay parked (no demand; the Phase-A plan forbids pulling the models
speculatively).

### Persistent gotchas (not session-specific)

- Research agent requires `pip install -e ".[research]"` (heavy deps:
  langchain, playwright, llama-index). Not installed by default.
- `impact_scan` node tested with mocked logic only — needs integration
  test against real LangGraph compile with `OPENAI_API_KEY`.
- `ollama-sentinel run` requires `ollama pull qwen3-embedding:4b` once on
  first use (~2.5 GB), or set `memory.semantic_recall: false` to fall back
  to the legacy exact-path recall.
- `embedding.models.consolidation` and `embedding.models.rerank` are
  pre-registered in the schema but UNWIRED. Do NOT pull `qwen3-embedding:8b`
  or any reranker model unless you're picking up Phase B or C — they sit
  in the YAML so future phases don't need another config migration.
- `_archive/` holds superseded snapshots
  (`ollama_sentinel_pre_memory_snapshot/`, `research_agent_orphans/`).
  Do not import from it. See `_archive/README.md` for provenance.
- The top-level working-tree directories `phind.phind-0.25.4/` and
  `config/` are gitignored cruft (third-party VSCode extension and
  unrelated Codex SQLite/Hypercorn data, respectively). Safe to
  `rm -rf` whenever; both kept around because the user denied the
  destructive `rm` last session.

### Recent landings

- 2026-06-04: **"Make findings actionable" arc — slices 1-2 merged, 3-4 in
  flight.** SURFACE (#14): `surface` command + `ollama_sentinel/sarif.py`
  (SARIF 2.1.0, excerpt relocation) + watcher auto-refresh. TRIAGE (#15):
  `findings` / `resolve` / `dismiss` verbs + `get_finding` / `get_open_findings`
  / `mark_resolved(resolution=...)`. REMEDIATE (slice 3): spec revised for
  safety after an adversarial readiness review (the 4-piece stack and its
  write-path safety properties);
  `docs/superpowers/specs/2026-06-03-remediate-fix-design.md`. STALE-PRUNE
  (slice 4): spec drafted; `docs/superpowers/specs/2026-06-04-stale-prune-design.md`.
  Same session landed four surgical polish branches off the merged slices:
  `fix/findings-stable-tiebreak` (id ASC tiebreak in `get_open_findings`),
  `fix/idempotent-close` (resolve/dismiss no longer flip an already-closed
  finding's resolution), `fix/guard-findings-corroboration` (best-effort
  corroboration lookup in `findings`), `test/dashboard-async-mock` (silence a
  mis-attributed unawaited-coroutine warning).
- 2026-05-30: **v0.2 Incident schema complete (Pieces 1-5).** Pieces 1-3
  (schema + migration + CRUD, post-commit hook + `install-hooks`/
  `record-commit`, `confirm` verb) merged to master as stacked PRs #8/#9/#10.
  Piece 4 — opt-in pytest plugin (`ollama_sentinel/pytest_plugin.py`, `pytest11`
  entry point) that turns a matching test failure into a `test_failure`
  Incident — and Piece 5 — `incidents` CLI verb (table/JSON, `--finding`
  scope) + these docs — landed as branches `feat/v02-piece-4-pytest-plugin`
  and `feat/v02-piece-5-incidents-cli`. Findings are model opinions; Incidents
  are corroborated events. Plan: `docs/superpowers/plans/2026-05-02-v02-incident-schema.md`.
- 2026-05-03: `run_dashboard` main loop hardened. Three bugs fixed: DB
  connection churn (open/close every tick → single persistent connection,
  reset on failure), blocking event loop (`_snapshot` now runs via
  `asyncio.to_thread`), no per-tick exception isolation (each data source
  now degrades independently). New `shutdown: Optional[asyncio.Event]`
  param for graceful external shutdown via cancellable sleep. 4 new tests
  added; all 13 dashboard tests pass. **Not yet tested live against a
  running sentinel by the user** — smoke-tested only (3s timeout run, exit 124).
- 2026-05-03: Config-load + embedding-timeout debugging session against
  a real watched project. Diagnosed cwd-shadowed stale YAML loading the
  wrong models; fixed README to make the two-terminal flow + cwd
  dependency explicit; added `embedding.timeout_seconds` YAML knob;
  right-sized embedder default 30s → 120s → 30s after measuring three
  cold-load regimes (warm-page-cache 2.2s, purged 2.0s, natural-idle
  6.4s). Open issue: sentinel review output is pattern-matched AI slop
  rather than grounded in file content — flagged, not fixed. Full
  retro: `docs/retros/2026-05-03-config-and-timeout-debugging.md`.
- 2026-05-01: Phase A landed. Hot-path embedder swapped from
  nomic-embed-text to qwen3-embedding:4b. EmbeddingConfig refactored to a
  named-role dict with extra='forbid'; consolidation and rerank roles
  pre-registered (schema-property pre-registration via merge-in-validator)
  but unwired. Legacy `embedding.model: foo` YAML auto-migrates with a
  one-shot deprecation warning that threatens hard-error in v0.3. Plan:
  `~/.claude/plans/yes-putting-both-moonlit-galaxy.md`. Spec:
  `docs/superpowers/plans/2026-05-01-phase-a-qwen3-hot-path-swap.md`.
  Phases B and C remain parked pending v0.2 Incident schema.
- 2026-05-01: CB-3 closed (commit 821b6b0). `research_agent`'s analyze
  node now consults prior webpage neighbors via `find_similar_webpages_sync`,
  alongside the existing `find_similar_queries_sync` call. New leaf module
  `research_agent/core/prompts.py` holds the pure formatter so it stays
  testable without the `[research]` extras. No new dependencies; sync
  wrapper degrades to token-overlap when no embedder is configured.
  Spec: `docs/superpowers/plans/2026-05-01-cb3-wire-find-similar-webpages.md`.
  Phases A/B/C of the broader Qwen3 embedding plan
  (`~/.claude/plans/yes-putting-both-moonlit-galaxy.md`) remain parked
  pending v0.2 Incident schema.
- 2026-05-01: Closed Issue #1 (TTY injectable), TR-1 (prompts.py leaf module),
  CB-2 (SemanticRetriever integration test). Merged `harden-ollama-sentinel-processing`
  to master. 355 tests passing.
- 2026-04-30: v0.1.0 cut + GitHub Release published. Repo made public.
  Casing fix `skidudeaa` → `Skidudeaa`. Four cheap follow-ups closed
  (CB-4, CB-6, CB-7, TR-2). Filed issue #1 for the TTY-branch test gap
  uncovered while resolving TR-2.
- 2026-04-29: Repo readiness review + cleanup pass. Doc/`gitignore`
  drift fixed. **Visual guide `docs/index.html` shipped** — single-file
  HTML infographic, deep ink + Fraunces + JetBrains Mono, left-margin
  time rail. Pinned at the top of CLAUDE.md, README, GUIDE.
- 2026-04-16: `ollama-sentinel dashboard` landed. Live Rich TUI of
  recent reviews + recurring violations, polls the DB read-only. See
  `ollama_sentinel/dashboard.py`.
- 2026-04-16: `ollama-sentinel triage` landed. Pipe tool output, get
  a local-model diagnosis with auto-extracted source context. Plan:
  `docs/superpowers/plans/2026-04-16-triage.md`.
- 2026-04-16: ContextBuilder landed. Prompt assembly + violation memory
  are now embedding-ranked and token-budgeted. Plan:
  `docs/superpowers/plans/2026-04-16-context-builder.md`.

### Open follow-ups

See `docs/superpowers/followups.md` for the canonical list with hashes.
The remaining work is captured in the "Pickable next moves" table above.
