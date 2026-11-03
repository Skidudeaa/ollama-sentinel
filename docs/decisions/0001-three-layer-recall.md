# 0001 — Three-layer recall cascade

**Status:** accepted
**Date:** 2026-05-01
**Supersedes:** none
**Superseded by:** none
**Tags:** recall, architecture
**Commit:** (structural recall commits from session — backfill SHA)
**PR:** none (direct to master)

## Context

The sentinel's `_get_ranked_prior_violations` had two paths: semantic
recall (embedding similarity across all unresolved findings) and
single-file recall (exact file-path match). `ViolationDB` also had a
`get_neighbors_unresolved(file_paths)` method that accepted multiple
paths — but nothing called it. The `ImportResolver` AST scanner existed
in `research_agent/tools/` but was not wired into the sentinel. The
README claimed "knows your blind spots" but structural awareness was
not delivered by code.

## Decision

Layer recall into a three-stage cascade in `FileProcessor._get_ranked_prior_violations`:

1. **Semantic recall** — cosine similarity via `get_neighbors_by_similarity`. Highest quality, requires embedder.
2. **Structural recall** — 1-hop import-graph neighbors via `ImportResolver.resolve_imports` + `resolve_dependents`, fed to `get_neighbors_unresolved`. Initially Python-only; extended on 2026-11-02 (`ollama_sentinel/context/import_resolver.py`) to support Python (`.py`), TypeScript/JavaScript (`.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`), and Swift (`.swift`).
3. **Single-file recall** — `get_unresolved(file_path)`. Always available.

Each layer is independently gated by config (`semantic_recall`,
`structural_recall`) and degrades silently on failure. First non-empty
result wins; no layer blocks review generation.

## Consequences

A finding on `utils.py` now surfaces when reviewing `app.py` (and vice
versa) via the import graph. The README's claim is load-bearing.
Update (2026-11-02): `ImportResolver` has been promoted into
`ollama_sentinel/context/import_resolver.py` (removing the cross-package
import from `research_agent/tools/`) and extended to cover TypeScript/JavaScript
and Swift in addition to Python.
The resolver's `_import_cache` goes stale during long-running watcher
sessions; restart-to-refresh is the current workaround.

## Alternatives considered

- **Promote ImportResolver first, then wire.** Correct but slower; the
  wiring was a half-day win and the promotion is a v0.3 concern.
- **Skip structural recall, rely on semantic only.** Semantic recall
  can't surface findings on files the embedder hasn't seen; structural
  recall catches the import-graph neighbors that semantic misses.
- **Two-layer only (semantic + single-file).** This was the status quo
  before the session. `get_neighbors_unresolved` was dead code.
