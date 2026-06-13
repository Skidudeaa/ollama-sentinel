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
ollama-sentinel fix 42              # localized fix for Finding 42: preview diff, write on confirm, resolve (fixed)
ollama-sentinel prune               # close findings whose flagged code is gone (preview + confirm, resolution='stale')

# Guardrails (project rules injected into reviews; Finding->Incident->Pattern rung)
ollama-sentinel guardrail add no-eval -a "Never call eval on untrusted input." --category security --path "src/*.py"
ollama-sentinel guardrail list      # active guardrails (--all / --status; -f json)
ollama-sentinel guardrail edit 3 --assertion "..."   # edit name/assertion/scope
ollama-sentinel guardrail disable 3 # disable | enable | dismiss (lifecycle)
ollama-sentinel guardrail candidates # detect recurring shapes (>=3 corroborated) w/ drafted assertion (on-demand)
ollama-sentinel guardrail promote 1 # confirm candidate #1 -> active guardrail (source=promoted)
ollama-sentinel guardrail reject 1  # dismiss candidate #1 so its shape isn't re-proposed

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
                             |- _get_active_guardrails() (active project rules)
                             |- format_prompt() -> build_review_context() recipe
                             |    MUST_FIT: active file / diff block
                             |    OPTIONAL: PROJECT GUARDRAILS (scope-filtered, ranked)
                             |    OPTIONAL: PRIOR UNRESOLVED (retriever-ranked)
                             |- chunk_content() -> chunk_by_lines (token-aware)
                             |- OllamaClient.generate_review() (httpx, tenacity retry)
                                 |
                           extract_findings() (LLM JSON + regex fallback)
                             |- attribute_guardrail_provenance() (best-effort: finding -> guardrail)
                             |- ViolationDB.persist_findings() (SQLite upsert,
                                populates embed_text + guardrail_id provenance)
                                 |
                           FileProcessor.save_review()
                             |- versioned output with history cleanup
```

### Guardrails (v0.3: Finding -> Incident -> Pattern)

Guardrails are curated, named NL rules the review model checks explicitly. They
are born two ways and converge on one active artifact (the Pattern rung from
`docs/VISION.md`):

```
   Developer authors directly ----------------------------\
   (guardrail add)                                          v
                                                      active guardrail
   >=3 distinct corroborated findings of one shape          ^   |
     -> guardrail candidates (on-demand clustering)         |   |- injected into reviews
        -> LLM-drafted assertion                            |   |     (scope filter + rank + budget)
        -> guardrail promote (confirm) ---------------------/   |- flagged findings carry provenance
        -> guardrail reject (suppress shape)                    v
                                                          ViolationDB.guardrails table
   evidence-integrity gate (counts_toward_strength): a guardrail's own findings
   reinforce a candidate only via a hard signal (test_failure/fix_commit) -> no echo
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
| `ollama_sentinel/violation_db.py` | SQLite-backed Finding persistence with upsert, `embed_text` column, `get_neighbors_by_similarity`; `guardrails` table + CRUD (`Guardrail`); finding `guardrail_id` provenance; `get_corroborated_findings` (clustering selector) |
| `ollama_sentinel/guardrails.py` | Guardrail shape clustering + candidate detection (leaf): `Candidate`, async `detect_candidates`, `draft_assertion`, `derive_scope`, `candidate_signature`/`filter_suppressed`, and the `counts_toward_strength` evidence-integrity gate |
| `ollama_sentinel/extractor.py` | LLM JSON extraction + regex fallback for parsing review findings |
| `ollama_sentinel/watcher.py` | FileSentinel, file watching, ignore logic, pipeline orchestration |
| `ollama_sentinel/models.py` | Pydantic v2 config models: Ollama/Embedding/Memory/Processing with validators |
| `ollama_sentinel/cli.py` | Typer CLI: run, review, init, report, triage, dashboard, confirm, incidents, install-hooks, record-commit, surface, findings, resolve, dismiss, fix, prune + `guardrail` sub-app (add/list/edit/disable/enable/dismiss, candidates/promote/reject) |
| `ollama_sentinel/remediate.py` | Localized fix generation for `fix <id>`: `splice_lines`/`parse_fix_response`/`build_fix_prompt` (pure) + `propose_fix` (I/O); bounds the model edit to the finding's exact whole-line span |
| `ollama_sentinel/pytest_plugin.py` | Opt-in pytest plugin: matches test failures to open Findings, records `test_failure` Incidents (`pytest11` entry point) |
| `ollama_sentinel/hooks.py` | Git post-commit hook installer + `record_commit` (links commits to open Findings) |
| `ollama_sentinel/dashboard.py` | Live Rich TUI for `ollama-sentinel dashboard` — polls reviews dir + ViolationDB read-only; includes a read-only active-guardrails panel (`active_guardrails`/`_guardrails_panel`) |
| `ollama_sentinel/sarif.py` | SARIF 2.1.0 surface: excerpt-based `relocate_finding`, `build_sarif` document, `generate_sarif_file` (read-only orchestration) — backs the `surface` command + watcher auto-refresh; `collect_stale_findings` (read-only) backs `prune` |
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
- `read_strict()` / `safe_write()` back the `fix` write path: same containment as
  `safe_read` but **raise** instead of degrading — strict UTF-8 (refuse, never
  corrupt non-text bytes), atomic `os.replace`, original mode preserved
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

### Repo state as of 2026-06-05 (last session)

- **v0.1.1 shipped**; repo public at <https://github.com/Skidudeaa/ollama-sentinel>.
- **Test suite:** run `pytest tests/ -q` for the live count (this session it was
  679 passed / 15 skipped, ~10s). Do **not** hardcode the number here again — it
  drifts every time tests land. Quote the command, not the count.
- **The "make findings actionable" arc** (surface → triage → remediate →
  stale-prune): **the full arc (slices 1-4) is MERGED to master.** surface
  (#14), triage (#15), remediate `fix <id>` (PRs #22-26, rebase-merged
  2026-06-04), and **stale-prune `prune` (PR #29, rebase-merged 2026-06-05)**
  all ship. The arc is complete.
- **Working tree should be clean.** If it isn't, `git status` first.
- **The visual guide (`docs/index.html`) is the canonical pitch surface.**
  Linked from README, GUIDE.md, and the v0.1.0 release notes.

### Resume here next time

1. **Sanity check.** `pytest tests/ -q` should be green (run it for the live
   count; ~803 / 16 skip on the guardrails stack tip — do not trust this number,
   it drifts).
2. **Pattern promotion → project guardrails is BUILT** (the north-star v0.3
   feature). All 8 plan units U1–U8 ship as a linear stacked-PR chain
   **#37→#38→#39→#40→#41→#42→#43→#44** (base master). **Not yet merged** — review
   and merge bottom-up; GitHub auto-retargets each child to master as its parent
   lands. The independent salvage fix is **PR #36**. This docs PR rides the tip.
3. **The "make findings actionable" arc is complete** (PR #29, 2026-06-05).
   OP-1 SIGHUP hot-reload (#32) and CB-1 (#33) also done. See
   `docs/superpowers/followups.md`.

### Pickable next moves (ordered by leverage)

The v0.3 Pattern-promotion leap is **built** (#37–#44, pending merge). What's left:

| # | Item | Effort | Risk | Notes |
|---|---|---|---|---|
| 1 | **Merge the guardrails stack** (#37→#44 bottom-up) + #36. Then the two transparent scope deferrals: (a) the *live dashboard pending-candidate view* (skipped in U7 to honor KTD4 — on-demand clustering only; a cached/slow-cadence panel would re-enable it); (b) confirm whether U8's *global* self-caused-soft exclusion should become *scoped-to-originating-guardrail* (stricter-is-safe today). | S–M | low | Both noted in the U7/U8 PR bodies. |
| 2 | **v0.3 shared substrate** — lift `ImportResolver` to shared infra, unify `Finding`/`ImpactItem`, bidirectional impact↔incident flow. | L | med | The moat play (see `docs/VISION.md`). Architectural; follows guardrails. |

Skip TR-3 — deliberate spec deviation, documented in followups.md. Qwen3
Phases B/C stay parked (no demand; the Phase-A plan forbids pulling the models
speculatively).

### Persistent gotchas (not session-specific)

- Research agent requires `pip install -e ".[research]"` (heavy deps:
  langchain, playwright, llama-index). Not installed by default.
- `impact_scan` now has a real integration test
  (`tests/test_impact_scan_integration.py`): drives the actual node closure
  inside a graph compiled by `build_workflow`, LLM boundary faked, against a
  temp repo. Gated by `importorskip` on the full `[research]` stack — runs
  under `pip install -e ".[research]"`, skips otherwise. (The older
  `test_research_agent.py` mirror-logic tests remain as fast unit checks.)
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

- 2026-06-13: **v0.3 Pattern promotion → project guardrails BUILT + MERGED
  (8-PR stack).** Executed the `docs/plans/2026-06-08-001-...-plan.md` plan
  end-to-end via `/ce-work`, TDD per unit, one stacked PR each off master:
  **Phase 1** — U1 storage+provenance (#37: `guardrails` table + CRUD + finding
  `guardrail_id` via additive migration), U2 CLI authoring/lifecycle (#38:
  `guardrail add/list/edit/disable/enable/dismiss` sub-app), U3 relevance-scoped
  injection (#39: `PROJECT GUARDRAILS` section above PRIOR UNRESOLVED, scope
  filter + rank + budget), U4 best-effort provenance capture (#40), U5 dashboard
  panel (#41). **Phase 2** — U6 on-demand shape clustering (#42:
  `ollama_sentinel/guardrails.py` + `get_corroborated_findings`), U7 candidate
  surfacing+curation (#43: `guardrail candidates/promote/reject` w/ LLM-drafted
  assertions + signature suppression), U8 evidence-integrity gate (#44:
  `counts_toward_strength` — self-caused findings reinforce only via
  test_failure/fix_commit, no echo). Plus full doc sweep (#45: README, GUIDE,
  VISION, index.html, CLAUDE.md). Suite 699→803 passed / 16 skip. All
  rebase-merged bottom-up #37→#45 to master 2026-06-13. Two transparent
  deferrals (open): live dashboard *pending*-candidate view (KTD4: clustering
  on-demand only) and U8 scoped-vs-global gate — both noted in the U7/U8 PRs.
- 2026-06-12: **Truncated grounded-review salvage (PR #36, merged 2026-06-13).**
  A grounded review that hits the output-token cap (`num_predict`) is cut
  mid-JSON by Ollama; the parse failure was misdiagnosed as "model ignored
  format" and every complete finding was discarded, degrading to the regex
  extractor. Now `OllamaClient.generate_with_model` reads `done_reason` and warns
  with the `num_predict` value on truncation, and `_parse_review_response` runs a
  string/escape-aware bracket scan (`_salvage_truncated_review`) to trim to the
  last complete finding object, close the document, and re-parse — summary + all
  complete findings survive; only the finding cut mid-generation is lost.
  `:cloud` schema-ignoring prose still degrades to the legacy extractor. 10 new
  tests. Reproduced live against `qwen3-coder:30b`.
- 2026-06-08: **Small-DX backlog drained + v0.3 Pattern-promotion planned.**
  Shipped OP-1 SIGHUP hot-reload (PR #32), the impact_scan integration test
  (PR #34), and salvaged grounding P1–P4 positives (PR #31); closed CB-1 as
  already-done (PR #33). Then took **Pattern promotion → project guardrails**
  brainstorm→plan: requirements at
  `docs/brainstorms/2026-06-08-pattern-promotion-guardrails-requirements.md`,
  plan at `docs/plans/2026-06-08-001-feat-pattern-promotion-guardrails-plan.md`
  (Deep, 8 units, 2 phases — Phase 1 U1–U5 independently shippable). Handoff:
  `docs/session-notes/2026-06-08-guardrails-planned-dx-backlog-drained-handoff.md`.
  **Next pickup: `/ce-work` that plan, Phase 1 first.**
- 2026-06-07: **`docs/VISION.md` refreshed (structural rewrite) + session
  handoff.** Replaced the stale v0.1.0+ snapshot, added a "making findings
  actionable (shipped)" section, preserved the v0.3 substrate / north-star
  narrative; new handoff at
  `docs/session-notes/2026-06-07-vision-refresh-and-arc-complete-handoff.md`.
  Docs-only; suite unchanged (689/15). Plan:
  `docs/plans/2026-06-07-001-docs-refresh-vision-and-handoff-plan.md`.
- 2026-06-05: **"Make findings actionable" arc COMPLETE — stale-prune `prune`
  SHIPPED + MERGED (PR #29, rebase-merged to master).** Two pieces, single PR
  (advisor-approved): `collect_stale_findings(db, watch_dir)` in `sarif.py`
  (read-only selector — returns open findings whose file is gone or whose
  verbatim excerpt no longer relocates via `relocate_finding`; reuses the
  `generate_sarif_file` content rule so `surface`/`prune` agree on "stale";
  omits `relocated` and `stored` findings) → `cli.prune` (Rich preview table,
  apply gate like `fix`: `--yes` applies, TTY prompts `[y/N]`, non-TTY previews
  only; closes with `resolution='stale'`, **no Incident** — `mark_resolved`
  only inserts an Incident when `fix_commit` is passed; read-only on source).
  10 new tests (5 sarif + 5 CLI). Full suite 689 passed / 15 skipped.
  Spec marked Implemented: `docs/superpowers/specs/2026-06-04-stale-prune-design.md`.
- 2026-06-04/05: **"Make findings actionable" arc — remediate `fix <id>` SHIPPED
  + MERGED; full arc slices 1-3 on master.** REMEDIATE built as a 4-piece stack,
  adversarially verified (Workflow, 4 lenses, 13 raw → 6 confirmed findings
  fixed incl. a real CRLF-rewrite bug), bot-review feedback (Codex+Copilot)
  addressed, then rebase-merged bottom-up as **PRs #22→#23→#24→#25→#26**. New:
  `ollama_sentinel/remediate.py` (`splice_lines`/`parse_fix_response`/
  `build_fix_prompt`/`propose_fix`), `utils.read_strict`+`safe_write` (atomic,
  UTF-8/CRLF-preserving, mode-preserving, O_NOFOLLOW), `sarif.Relocation.exact`,
  `cli.fix`. `fix` writes ONLY to an exact whole-line excerpt-verified span,
  always shows a diff, never writes without `[y/N]` or `--yes`, no commit.
  Same session, MERGED to master: `surface` (#14) and `triage` (#15) were
  already in; plus four polish PRs — `get_open_findings` id-tiebreak (#18),
  idempotent resolve/dismiss (#19), best-effort `findings` corroboration (#27,
  re-opened from auto-closed #20), dashboard unawaited-coroutine warning (#21);
  CLAUDE.md doc-drift refresh (#17); stale-prune spec (#16, slice 4).
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
