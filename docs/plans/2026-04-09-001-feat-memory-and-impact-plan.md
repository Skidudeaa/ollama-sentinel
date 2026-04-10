---
title: "feat: Add cumulative violation memory and dependency-aware impact mapping"
type: feat
status: completed
date: 2026-04-09
---

# feat: Add Cumulative Violation Memory and Dependency-Aware Impact Mapping

## Overview

Transform ollama-sentinel from a stateless file-review tool into an indispensable development companion by adding two capabilities:

1. **Cumulative Violation Memory (Sentinel)** — Parse review findings into structured records, persist them in SQLite, inject prior unresolved findings into future review prompts, and surface recurring violations via a new `report` command.

2. **Dependency-Aware Impact Mapping (Research Agent)** — When researching library migrations, CVEs, or API changes, produce a ranked list of every affected location in the user's codebase with exact call sites, severity classification, and suggested fixes — not a generic essay.

Both features exploit the same structural advantage: a persistent local tool that accumulates project-specific knowledge no cloud service can replicate.

## Problem Frame

**Sentinel today:** Each review sees exactly one file in isolation with zero memory. The output is raw markdown dropped into a directory no developer opens. There is no way to know that the same class of bug has appeared four times in two weeks. Every review starts from scratch.

**Research agent today:** Code context integration is a vector-similarity search over 512-token chunks. The agent cannot navigate import graphs, identify affected call sites, or produce structured impact analysis. Its output is indistinguishable from asking ChatGPT the same question.

**The gap:** The path from "nice demo" to "can't uninstall" is **memory + cross-file awareness**. The infrastructure for both exists in the codebase (watchfiles for temporal coverage, LlamaIndex for code access, diskcache for persistence) but is not connected.

## Requirements Trace

- R1. Review findings are parsed into structured records (file, line range, category, severity)
- R2. Structured findings are persisted in a local SQLite database
- R3. Prior unresolved findings for a file and its neighbors are injected into review prompts
- R4. A `report` command surfaces recurring violations ranked by frequency
- R5. Research queries about library changes produce structured impact analysis with file paths and line numbers
- R6. Impact analysis uses AST-based import graph resolution, not just vector similarity
- R7. Impact analysis output includes severity classification and suggested migration actions
- R8. Past impact analyses are persisted and reused when the same library/topic is queried again

## Scope Boundaries

- **In scope:** SQLite persistence for sentinel, finding parser, prompt injection, `report` CLI command, AST import resolver, structured impact output, impact memory
- **Out of scope:** IDE integrations, web UI, notification webhooks, real-time dashboard, team/multi-user features, natural language follow-up conversations on findings
- **Out of scope:** Replacing the LlamaIndex vector search — the AST resolver augments it, not replaces it
- **Out of scope:** Connecting the two modules — they remain architecturally independent per CLAUDE.md convention

## Context & Research

### Relevant Code and Patterns

- `ollama_sentinel/processor.py` — `format_prompt()` (line 178) is the injection point for prior violations; `save_review()` (line 274) is where finding extraction happens post-review
- `ollama_sentinel/models.py` — Pydantic config models; new `MemoryConfig` goes here
- `ollama_sentinel/cli.py` — Typer CLI; `report` command follows pattern of `run`/`review`/`init`
- `research_agent/core/workflow.py` — `code_search` node (line 287) is the replacement target for impact analysis
- `research_agent/tools/code_context.py` — current vector-only code search; AST resolver augments this
- `research_agent/tools/memory.py` — `EnhancedMemoryStore` is an in-memory stub with no real persistence
- `research_agent/utils/cache.py` — diskcache with JSON serialization; works but is flat K-V, not structured
- `research_agent/core/models.py` — dataclasses for `ContentItem`, `ResearchSession`, `ResearchStep`

### External References

- Python `ast` module — stdlib, no new dependency needed for import graph resolution
- `aiosqlite` — async SQLite for the sentinel's async architecture (or stdlib `sqlite3` with `asyncio.to_thread`)
- LLM structured output extraction — use the existing Ollama model with a focused extraction prompt to parse findings from review markdown

## Key Technical Decisions

- **SQLite over diskcache for violation storage**: Violations need structured queries (by file, by category, by date range, by recurrence count). diskcache is a flat K-V store. SQLite is already a transitive dependency via diskcache and requires no new install.
- **stdlib `sqlite3` + `asyncio.to_thread` over aiosqlite**: Keeps dependency count minimal. The sentinel already uses `asyncio.to_thread` for file I/O. One less package to install.
- **LLM-based finding extraction over regex parsing**: Different Ollama models produce different markdown structures. A focused extraction prompt asking the model to output structured JSON is more robust than brittle regex. The extraction uses the same Ollama model that generated the review.
- **AST import resolution over full language server**: Python's `ast` module handles Python files with zero dependencies. Other languages are out of scope for v1. The resolver walks `import` and `from...import` statements to build a file-level dependency graph.
- **New `impact_scan` workflow node over modifying `code_search`**: Keeps the existing vector search intact. The new node runs after `code_search` and augments results with structural analysis. This is additive, not destructive.

## Open Questions

### Resolved During Planning

- **Where to inject prior violations in the prompt?** In the user message constructed by `format_prompt()`, as a `PRIOR UNRESOLVED ISSUES` block before the code content. The system prompt stays per-role.
- **How to handle finding extraction failures?** Log a warning and save the raw review as before. Finding extraction is best-effort; it must never block review persistence.
- **Which files count as "neighbors" for violation injection?** Files that import or are imported by the changed file, determined by a lightweight import scan. Limit to 1-hop neighbors to keep prompt size bounded.

### Deferred to Implementation

- **Exact JSON schema for the extraction prompt**: Depends on what fields the model reliably produces. Start with `{file, line_start, line_end, category, severity, description}` and iterate.
- **Optimal prompt injection format**: Whether to use markdown tables, bullet lists, or structured blocks for prior violations. Determine empirically by testing with the configured Ollama model.
- **AST fallback for non-Python files**: v1 targets Python only. The interface should accept a language parameter so future resolvers can be added.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

### Phase 1: Sentinel Violation Memory

```
                    ┌─────────────────────┐
                    │   generate_review()  │
                    └──────────┬──────────┘
                               │ raw review markdown
                               ▼
                    ┌─────────────────────┐
                    │  extract_findings() │ ◄── LLM extraction prompt
                    └──────────┬──────────┘
                               │ List[Finding]
                          ┌────┴────┐
                          ▼         ▼
              ┌──────────────┐  ┌──────────────┐
              │ save_review()│  │ ViolationDB   │
              │ (as before)  │  │ .persist()    │
              └──────────────┘  └──────────────┘
                                       │
    ┌──────────────────────────────────┘
    ▼
┌──────────────────────────────────────┐
│  Next review: format_prompt() injects │
│  prior findings from ViolationDB     │
└──────────────────────────────────────┘
```

### Phase 2: Research Agent Impact Mapping

```
analyze → search → read → code_search → impact_scan → synthesize → verify
                                              │
                                    ┌─────────┴──────────┐
                                    │ 1. Extract entities │ (lib names, functions, classes)
                                    │    from web research│
                                    │ 2. AST import graph │ (resolve Python imports)
                                    │ 3. Match entities   │ (find call sites in graph)
                                    │ 4. Classify severity│
                                    │ 5. Suggest actions  │
                                    └─────────┬──────────┘
                                              │
                                    ImpactAnalysis (structured)
```

## Implementation Units

### Phase 1: Sentinel — Cumulative Violation Memory

- [x] **Unit 1: ViolationDB — SQLite persistence layer**

**Goal:** Create a SQLite-backed storage layer for structured violation records.

**Requirements:** R1, R2

**Dependencies:** None

**Files:**
- Create: `ollama_sentinel/violation_db.py`
- Modify: `ollama_sentinel/models.py` (add `MemoryConfig`)
- Test: `tests/test_violation_db.py`

**Approach:**
- New `ViolationDB` class wrapping stdlib `sqlite3`
- Schema: `findings` table (id, file_path, line_start, line_end, category, severity, description, first_seen, last_seen, occurrence_count, resolved)
- Methods: `persist_findings(file_path, findings)`, `get_unresolved(file_path)`, `get_neighbors_unresolved(file_paths)`, `get_recurring(min_count, limit)`, `mark_resolved(finding_id)`
- Upsert logic: if same file + line range + category exists, increment `occurrence_count` and update `last_seen`; otherwise insert
- All DB operations wrapped in `asyncio.to_thread` for async compatibility
- `MemoryConfig` Pydantic model with `enabled: bool = True`, `db_path: str = ".ollama_reviews/memory.db"` added to `SentinelConfig`

**Patterns to follow:**
- Pydantic model conventions in `ollama_sentinel/models.py`
- `asyncio.to_thread` wrapping pattern already used in `processor.py` and `watcher.py`

**Test scenarios:**
- Happy path: persist 3 findings, retrieve by file path, verify all fields stored correctly
- Happy path: `get_recurring(min_count=2)` returns only findings with occurrence_count >= 2
- Edge case: persist same finding twice — occurrence_count increments to 2, last_seen updates
- Edge case: persist finding for file not yet in DB — creates new record
- Edge case: empty findings list — no error, no DB writes
- Error path: invalid db_path directory — raises clear error at init time
- Integration: `get_neighbors_unresolved(["a.py", "b.py"])` returns findings from both files

**Verification:**
- All tests pass; DB file is created on disk; findings survive process restart

---

- [x] **Unit 2: Finding extractor — parse review into structured records**

**Goal:** Extract structured violation records from raw Ollama review markdown using a focused LLM extraction prompt.

**Requirements:** R1

**Dependencies:** Unit 1 (for the Finding dataclass)

**Files:**
- Create: `ollama_sentinel/extractor.py`
- Test: `tests/test_extractor.py`

**Approach:**
- New `extract_findings(review_text, file_path, ollama_client, model_role)` async function
- Sends a focused extraction prompt to the same Ollama model: "Given this code review, extract each distinct finding as JSON..."
- Parses the model's JSON response into `Finding` dataclass instances
- Falls back gracefully: if extraction fails (malformed JSON, timeout), returns empty list and logs warning
- Keep extraction prompt minimal and well-bounded to work with smaller local models

**Patterns to follow:**
- `OllamaClient.generate_review()` pattern for API calls
- tenacity retry pattern already on the client

**Test scenarios:**
- Happy path: well-formed review with 3 findings → extracts 3 Finding objects with correct fields
- Happy path: review with no actionable findings ("No issues found") → returns empty list
- Edge case: model returns malformed JSON → returns empty list, logs warning
- Edge case: model returns JSON array with missing fields → skips malformed entries, returns valid ones
- Error path: Ollama API timeout during extraction → returns empty list, logs warning
- Integration: extraction prompt includes file_path and it appears in each Finding's file_path field

**Verification:**
- Extraction works with mock HTTP responses; graceful degradation on all error paths

---

- [x] **Unit 3: Prompt injection — include prior violations in review context**

**Goal:** When generating a review, query ViolationDB for prior unresolved findings and inject them into the prompt so the model can reference and escalate recurring issues.

**Requirements:** R3

**Dependencies:** Unit 1, Unit 2

**Files:**
- Modify: `ollama_sentinel/processor.py` (format_prompt, generate_review)
- Test: `tests/test_processor.py` (add new test class)

**Approach:**
- `FileProcessor.__init__` accepts an optional `ViolationDB` instance
- Before calling `format_prompt`, query `violation_db.get_unresolved(file_path)` for the changed file
- Optionally query 1-hop import neighbors (use a lightweight `ast.parse` scan of the changed file's imports)
- `format_prompt` gains an optional `prior_violations` parameter; when present, prepends a `PRIOR UNRESOLVED ISSUES` block to the user message
- The block formats each violation as: `- [{severity}] {category} at line {line_start}: {description} (seen {count}x since {first_seen})`

**Patterns to follow:**
- Existing `format_prompt` parameter extension pattern (already has `chunk_text`, `chunk_index`, `total_chunks`)
- `asyncio.to_thread` for the `ast.parse` import scan

**Test scenarios:**
- Happy path: 2 prior violations injected → prompt contains "PRIOR UNRESOLVED ISSUES" with both entries
- Happy path: no prior violations → prompt is unchanged from current behavior
- Edge case: violation_db is None (memory disabled) → prompt is unchanged
- Edge case: prior violations exist but for a different file → not injected
- Integration: full flow — generate_review with violation_db, verify prompt sent to Ollama includes prior violations (use httpx_mock)

**Verification:**
- Prompts with and without violations are correct; no regression on existing prompt tests

---

- [x] **Unit 4: Persist findings after each review**

**Goal:** After each review, extract findings and persist them to ViolationDB.

**Requirements:** R1, R2

**Dependencies:** Unit 1, Unit 2, Unit 3

**Files:**
- Modify: `ollama_sentinel/watcher.py` (process_change)
- Modify: `ollama_sentinel/watcher.py` (FileSentinel.__init__ to create ViolationDB)
- Test: `tests/test_watcher.py` (add integration tests)

**Approach:**
- `FileSentinel.__init__` creates a `ViolationDB` if `config.memory.enabled`
- In `process_change`, after `generate_review` returns and before `save_review`, call `extract_findings` and then `violation_db.persist_findings`
- Both extraction and persistence are best-effort: failures are logged but do not block review saving
- Pass `violation_db` to `FileProcessor` for prompt injection (Unit 3)

**Test scenarios:**
- Happy path: process_change with memory enabled → findings extracted and persisted (mock Ollama for both review and extraction)
- Happy path: process_change with memory disabled → no extraction attempted
- Error path: extraction fails → review still saved, warning logged
- Error path: DB persist fails → review still saved, warning logged

**Verification:**
- End-to-end: file change → review generated → findings extracted → findings in DB → next review includes prior findings in prompt

---

- [x] **Unit 5: `report` CLI command**

**Goal:** Add `ollama-sentinel report` command that reads the violation database and prints a ranked summary of recurring violations.

**Requirements:** R4

**Dependencies:** Unit 1

**Files:**
- Modify: `ollama_sentinel/cli.py` (add report command)
- Test: `tests/test_cli.py` (add report tests)

**Approach:**
- New `@app.command()` decorated function `report`
- Accepts `--config` (for DB path from config), `--min-count` (default 2), `--limit` (default 20), `--format` (table/json)
- Queries `ViolationDB.get_recurring(min_count, limit)`
- Renders output using `rich.table.Table` for terminal display or JSON for piping
- No Ollama connection needed; purely reads the local DB
- Exits with helpful message if DB doesn't exist yet

**Patterns to follow:**
- Existing Typer command patterns in `cli.py`
- `rich.console.Console` for output formatting

**Test scenarios:**
- Happy path: DB with 5 recurring violations → table output shows all 5 ranked by occurrence
- Happy path: `--format json` → valid JSON array output
- Edge case: empty DB → friendly "No violations recorded yet" message
- Edge case: DB file doesn't exist → friendly "Run reviews first" message
- Edge case: `--min-count 5` filters out low-frequency violations

**Verification:**
- CLI produces correct output for populated and empty databases

---

### Phase 2: Research Agent — Dependency-Aware Impact Mapping

- [x] **Unit 6: AST import graph resolver**

**Goal:** Build a Python import graph resolver that maps file-level dependencies using `ast.parse`.

**Requirements:** R6

**Dependencies:** None (independent of Phase 1)

**Files:**
- Create: `research_agent/tools/import_resolver.py`
- Test: `tests/test_import_resolver.py`

**Approach:**
- New `ImportResolver` class that takes a repo root path
- `resolve_imports(file_path)` → returns list of resolved file paths that the file imports
- `resolve_dependents(file_path)` → returns list of files that import the given file
- `build_graph(entry_files)` → returns a dict mapping each file to its imports and dependents
- Uses `ast.parse` to walk `Import` and `ImportFrom` nodes
- Resolves module paths to file paths using `importlib.util.find_spec` logic or path heuristics
- Handles relative imports (`.module`), package imports, and missing files gracefully
- Python-only for v1; accepts a `language` parameter for future extension

**Patterns to follow:**
- Dataclass-based return types following `research_agent/core/models.py` conventions

**Test scenarios:**
- Happy path: file with 3 imports → resolves all 3 to file paths
- Happy path: `resolve_dependents` on a utility file → returns all files that import it
- Edge case: relative import (`from .utils import helper`) → resolves correctly
- Edge case: import of external package (not in repo) → skipped, not in results
- Edge case: file with syntax errors → returns empty list, logs warning
- Edge case: circular imports → detected without infinite loop
- Happy path: `build_graph` produces correct bidirectional mapping

**Verification:**
- Resolver works on a synthetic multi-file Python project in tmp_path

---

- [x] **Unit 7: Impact analysis data models**

**Goal:** Define structured data models for impact analysis results.

**Requirements:** R5, R7

**Dependencies:** None

**Files:**
- Modify: `research_agent/core/models.py` (add ImpactItem, ImpactAnalysis)
- Modify: `research_agent/core/workflow.py` (extend AgentState TypedDict)
- Test: `tests/test_research_agent.py` (add model tests)

**Approach:**
- `ImpactItem` dataclass: `file_path`, `line_number`, `pattern` (the code that matches), `severity` (HIGH/MEDIUM/LOW), `action` (suggested migration), `entity` (the library entity that's affected)
- `ImpactAnalysis` dataclass: `query`, `entity_count`, `affected_files`, `items: List[ImpactItem]`, `timestamp`
- Add `impact_analysis: Optional[ImpactAnalysis]` to `AgentState`

**Patterns to follow:**
- Existing dataclass patterns in `research_agent/core/models.py` (ContentItem, ResearchStep)

**Test scenarios:**
- Happy path: ImpactItem and ImpactAnalysis construct with all fields
- Edge case: ImpactAnalysis with empty items list
- Happy path: dataclass serializes to dict correctly (for cache persistence)

**Verification:**
- Models instantiate, serialize, and appear in AgentState type definition

---

- [x] **Unit 8: `impact_scan` workflow node**

**Goal:** Add a new workflow node that combines web research results with AST-based code analysis to produce structured impact analysis.

**Requirements:** R5, R6, R7

**Dependencies:** Unit 6, Unit 7

**Files:**
- Modify: `research_agent/core/workflow.py` (add impact_scan node after code_search)
- Test: `tests/test_research_agent.py` (add impact scan logic tests)

**Approach:**
- New `impact_scan(state: AgentState) -> AgentState` function inside `build_workflow()`
- Step 1: Use LLM to extract concrete entities (library names, function names, class names) from `state["content_items"]` and `state["answer"]`
- Step 2: Use `ImportResolver.build_graph()` to map the repo's import structure
- Step 3: For each entity, search the import graph and vector index results for matching call sites using string matching against the AST-parsed source
- Step 4: Classify severity based on whether the entity is deprecated, removed, or security-critical
- Step 5: Use LLM to suggest migration action for each HIGH severity item
- Wire into graph: `code_search` → `impact_scan` → `synthesize`
- Impact scan is skipped (passes state through) if no entities are extracted

**Patterns to follow:**
- Existing workflow node pattern (closure inside `build_workflow`, reads/writes AgentState)
- Error handling pattern: try/except with `session.fail_step`, return state with `step = "failed"`

**Test scenarios:**
- Happy path: mock state with content_items mentioning a library → impact_scan extracts entities and produces ImpactAnalysis
- Edge case: no entities extracted from research → impact_scan passes state through unchanged
- Edge case: entities found but no matching code in repo → ImpactAnalysis with empty items
- Error path: AST parse failure on a file → that file skipped, others still analyzed
- Integration: verify_router logic unchanged — impact_scan is transparent to the verify/refine loop

**Verification:**
- Impact analysis appears in state; synthesize node receives it; output format is structured

---

- [x] **Unit 9: Structured impact output in synthesis**

**Goal:** When impact analysis is available, format the synthesis output as a structured impact report instead of a narrative essay.

**Requirements:** R5, R7

**Dependencies:** Unit 7, Unit 8

**Files:**
- Modify: `research_agent/tools/synthesis.py` (add impact-aware template)
- Modify: `research_agent/core/workflow.py` (pass impact_analysis to synthesize)
- Test: `tests/test_research_agent.py` (add synthesis format tests)

**Approach:**
- When `state["impact_analysis"]` has items, synthesis uses an impact-focused template:
  - `IMPACT ANALYSIS: N call sites across M files`
  - Grouped by severity (HIGH → MEDIUM → LOW)
  - Each item: `file:line  pattern → suggested action`
  - `SUGGESTED FIRST COMMIT:` section for HIGH severity items
- When no impact analysis, falls back to existing narrative synthesis
- The impact template is a second Handlebars template alongside the existing one

**Test scenarios:**
- Happy path: state with 5 ImpactItems → output contains "IMPACT ANALYSIS" header, severity grouping, file:line references
- Happy path: state without impact_analysis → output is narrative (unchanged behavior)
- Edge case: impact_analysis with only LOW severity items → no "SUGGESTED FIRST COMMIT" section
- Edge case: empty impact_analysis items → falls back to narrative

**Verification:**
- Output format matches the structured impact report specification

---

- [x] **Unit 10: Impact memory persistence**

**Goal:** Persist past impact analyses so repeated queries about the same library reuse prior results for files that haven't changed.

**Requirements:** R8

**Dependencies:** Unit 7, Unit 8

**Files:**
- Modify: `research_agent/tools/memory.py` (add ImpactRecord storage)
- Modify: `research_agent/core/workflow.py` (check/store impact memory in impact_scan)
- Modify: `research_agent/utils/cache.py` (used for persistence)
- Test: `tests/test_research_agent.py` (add memory persistence tests)

**Approach:**
- Store `ImpactAnalysis` in the existing `Cache` with key `impact_{normalized_query}`
- In `impact_scan`, before running full analysis: check cache for prior impact analysis of the same topic
- If cached result exists and affected files haven't changed (compare git mtime or file hash), reuse the cached results
- If files have changed, re-analyze only the changed files and merge with cached results
- Cache TTL follows the existing `cache_ttl_hours` config

**Test scenarios:**
- Happy path: first query stores impact analysis in cache; second identical query retrieves it
- Happy path: file changes between queries → re-analyzes changed file, keeps others
- Edge case: cache expired → full re-analysis
- Edge case: cache hit but all files changed → effectively full re-analysis

**Verification:**
- Second query for same topic is faster; results include both cached and fresh analysis

## System-Wide Impact

- **Interaction graph:** Sentinel: `process_change` → `extract_findings` → `ViolationDB.persist` is a new async chain. Research agent: `impact_scan` is a new node between `code_search` and `synthesize`, connected by unconditional edges.
- **Error propagation:** Both features are designed as best-effort layers. Extraction/analysis failures must never block the existing review/research pipeline. All new error paths log warnings and fall through.
- **State lifecycle risks:** SQLite concurrent writes from multiple simultaneous reviews. Mitigate with WAL mode and connection-per-operation pattern (no long-lived connections).
- **API surface parity:** The `report` command is sentinel-only. Impact mapping is research-agent-only. No cross-module API surface.
- **Unchanged invariants:** Existing review generation, saving, and output formats are unchanged. Existing research workflow (analyze → search → read → code_search → synthesize → verify) continues to work; impact_scan is additive.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Small Ollama models may produce unreliable structured extraction | Use a focused, well-bounded extraction prompt. Fall back to empty findings on parse failure. Allow model-specific prompt templates in config. |
| AST import resolution only covers Python | Scope to Python for v1. Accept `language` parameter for extensibility. Other languages deferred. |
| SQLite concurrent writes from parallel reviews | Use WAL mode. Open/close connections per operation rather than holding long-lived connections. |
| Impact analysis adds latency to research queries | Impact scan is skippable when no entities are found. Cache results aggressively. |
| Finding extraction doubles Ollama API calls per review | Make extraction configurable (`memory.enabled`). The extraction call is smaller and faster than the review itself. |

## Phased Delivery

### Phase 1: Sentinel Violation Memory (Units 1-5)
Ship independently. The sentinel gains memory, prompt injection, and the `report` command. This is the highest-impact change because it transforms every review from stateless to cumulative.

### Phase 2: Research Agent Impact Mapping (Units 6-10)
Ship independently after Phase 1. The research agent gains AST resolution, structured impact output, and memory. This is the higher-complexity change but has no dependency on Phase 1.

## Sources & References

- Related code: `ollama_sentinel/processor.py` (format_prompt, generate_review, save_review)
- Related code: `research_agent/core/workflow.py` (build_workflow, code_search node)
- Related code: `research_agent/tools/code_context.py` (CodeSearchTool)
- Related code: `research_agent/tools/memory.py` (EnhancedMemoryStore — stub)
- Python ast module: https://docs.python.org/3/library/ast.html
- SQLite WAL mode: https://www.sqlite.org/wal.html
