# 2026-11-02 — Bake-verdict fixes, active workflow, and multi-language recall handoff

This session executed the full stack of improvements identified from the
2026-08-25 dogfood bake verdict, followed by active review/remediation
ergonomics, multi-language structural recall, database repair tooling, two
`/code-soak` passes, and a full documentation refresh.

## Summary of what shipped

### 1. Tier 1 & Tier 2 — Bake-verdict reliability & signal-to-noise (`0088785`)

1. **Hardened legacy prose extraction (`ollama_sentinel/extractor.py`):**
   - Added `_strip_code_spans()` to strip fenced (` ```...``` `) and inline
     (`` `...` ``) code spans before scanning `_SEVERITY_KEYWORDS` and
     `_CATEGORY_KEYWORDS`. Fixes the #1 bake defect where CSS `.critical`,
     `{ critical_high: 'HH' }`, or `catch (error)` inside quoted source
     promoted 71% of soma's critical tier.
   - Added `_EXPLICIT_SEVERITY_PATTERN` and `_EXPLICIT_CATEGORY_PATTERN` so
     explicit `Severity:` / `Category:` labels take precedence over keyword
     scanning.
   - Added `_NEGATED_SEVERITY_PATTERN` so phrases like `"not critical"` do not
     trigger `"critical"`.
   - Added `_NON_ISSUE_PATTERN` to drop non-issue praise blocks (`"looks good"`,
     `"no issues found"`).
   - Added `synthesize_excerpt(file_content, line_start, line_end)` and wired it
     into `extract_findings_legacy` (plus `ViolationDB.persist_findings`
     upsert backfill) so `:cloud` models that degrade to regex extraction still
     populate `verbatim_excerpt` — unblocking `fix`, `prune`, and `surface`
     relocation.
   - Added `_strip_prompt_prefixes()` in `validate_findings` so grounded JSON
     excerpts that accidentally copy prompt `N: ` line numbers or diff `+`
     markers still validate and store clean source lines with indentation
     preserved.

2. **Auto-reconcile open findings on save (`ollama_sentinel/sarif.py`, `ollama_sentinel/watcher.py`, `ollama_sentinel/violation_db.py`):**
   - Added `reconcile_file_findings(db, file_rel_path, content)` in `sarif.py`
     and wired it into `FileSentinel.process_change` before `generate_review`.
   - On every file save, open findings whose `verbatim_excerpt` vanished are
     auto-closed (`resolution='stale'`), and findings whose lines shifted are
     updated in place via `ViolationDB.update_finding_location` (which merges
     any duplicate unresolved row at the target `(file_path, line_start,
     line_end, category)` span and repoints incidents).
   - Fixed `ViolationDB.get_recurring` to filter `AND resolved = 0` in SQL
     before `LIMIT`.

3. **Grounded `git_diff_mode` & watch-scope hygiene (`ollama_sentinel/processor.py`, `ollama_sentinel/watcher.py`, `ollama_sentinel/config.py`):**
   - `FileProcessor.prepare_file_content` now always populates
     `file_change.content` even when `git_diff_mode` populates
     `file_change.diff`, and `generate_review` numbers diff lines with real
     new-file line numbers via `_number_diff_lines` so grounded reviews and
     legacy excerpt synthesis work in diff mode.
   - Expanded `_BUILTIN_IGNORE_PATTERNS` in `watcher.py` and default
     `ignore_patterns` in `config.py` to exclude `.jsonl`, `.csv`, `.tsv`,
     `.svg`, `.map`, `.min.js`, `.min.css`, `.worktrees/`, `.playwright-cli/`,
     `.design-handoff/`, and `.aider*`.

4. **Interactive Control Center triage (`ollama_sentinel/dashboard.py`, `ollama_sentinel/dashboard_input.py`):**
   - Added `r` (resolve as `fixed`), `d` (dismiss as `dismissed`), and `c`
     (corroborate as `manual_confirm` Incident) in the Patterns panel and
     Detail modal.
   - Filtered resolved findings out of `top_violations`.

### 2. Tier 3 — In-dashboard fix preview, multi-language structural recall, and `check` (`f114cc5`)

1. **In-Dashboard Fix Preview & Apply (`ollama_sentinel/dashboard.py`, `ollama_sentinel/dashboard_input.py`):**
   - Pressing `f` on a selected pattern (or in Detail mode) calls
     `generate_dashboard_fix_preview`, which verifies the TOCTOU file signature,
     relocates the exact whole-line span via `relocate_finding`, calls
     `remediate.propose_fix`, and opens the Detail modal with a unified diff.
   - Pressing `y` calls `apply_dashboard_fix` (`safe_write` + `mark_resolved`
     with `resolution='fixed'`); pressing `n` or `Esc` cancels.

2. **Multi-Language Structural Recall (`ollama_sentinel/context/import_resolver.py`, `ollama_sentinel/processor.py`):**
   - Promoted `ImportResolver` into `ollama_sentinel/context/import_resolver.py`
     (removing the cross-package import from `research_agent.tools`) and
     extended 1-hop import graph resolution (`resolve_imports` +
     `resolve_dependents`) to support:
     - **Python (`.py`):** AST `Import` and `ImportFrom` (absolute + relative).
     - **TypeScript / JavaScript (`.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`):**
       ES `import`/`export ... from`, dynamic `import()`, CommonJS `require()`,
       relative paths, `@/` root aliases, extensionless resolution, TS ESM `.js`
       → `.ts`/`.tsx` mapping, and directory `index.*` barrel files.
     - **Swift (`.swift`):** Intra-repo nominal type declaration (`struct`,
       `class`, `enum`, `protocol`, `actor`) and reference resolution across
       `.swift` files.

3. **Git Working-Tree & Staged Check Command (`ollama_sentinel/cli.py`):**
   - Added `ollama-sentinel check [--staged] [--model <role>] [--fail-on <severity>]`
     to review modified/untracked (or `--staged`) git files and exit `1` when
     open findings on checked files meet or exceed `--fail-on`.

### 3. Cache invalidation, pre-commit hook installer, database `repair`, and `/code-soak` (`eea9c4a` + soak fixes)

1. **Incremental `ImportResolver.invalidate(file_path)` (`ollama_sentinel/context/import_resolver.py`, `ollama_sentinel/processor.py`):**
   - Called automatically in `FileProcessor._resolve_import_neighbors` before
     resolving imports/dependents.
   - Refreshes modified files in `_import_cache`, resets `_import_cache` when a
     newly created file may satisfy previously unresolved caller imports,
     removes deleted files from both `_import_cache` keys and cached caller
     import lists, and resets `_swift_type_map` when `.swift` files change.

2. **Pre-commit hook option (`ollama_sentinel/hooks.py`, `ollama_sentinel/cli.py`):**
   - `ollama-sentinel install-hooks --pre-commit [--fail-on <severity>]`
     installs `.git/hooks/pre-commit` running
     `ollama-sentinel check --staged --fail-on <severity>` alongside the
     existing `post-commit` hook, without overwriting existing user hooks.

3. **Historical database repair (`ollama_sentinel/extractor.py`, `ollama_sentinel/violation_db.py`, `ollama_sentinel/cli.py`):**
   - Added `reclassify_description()` and
     `ViolationDB.repair_historical_findings(watch_dir, apply=...)` backing
     `ollama-sentinel repair [--yes]`.
   - Re-evaluates open findings whose stored `severity` or `category` matched a
     keyword inside a fenced/inline code span or negated phrase, backfills
     empty `verbatim_excerpt` columns from disk, and merges colliding duplicate
     rows.

4. **Second `/code-soak` catches:**
   - Fixed `_collect_git_changed_files(..., staged=True)` and `check` in
     `ollama_sentinel/cli.py` to load the staged git index blob (`git show
     :<rel>`) into `FileChange.content` so `check --staged` validates and
     synthesizes excerpts against the staged index content even when the
     working tree has unstaged edits on the same file.
   - Fixed `ImportResolver.invalidate(file_path)` when a newly created module
     satisfies previously unresolved caller imports or when a deleted module
     leaves dangling paths in cached caller import lists.

## Verification

- Full test suite: `pytest tests/ -q` → **870 passed, 16 skipped**.
- All documentation updated (`README.md`, `CLAUDE.md`, `docs/GUIDE.md`,
  `docs/VISION.md`, `docs/decisions/0001-three-layer-recall.md`,
  `docs/superpowers/followups.md`, `docs/index.html`).

## Next steps when resuming

1. Run `ollama-sentinel repair --yes` against any preserved bake SQLite
   databases (`soma`, `theater6`, `dictum`) if you wake the fleet back up, so
   historical false-criticals and missing excerpts are repaired in place.
2. If you want pre-commit gating in a watched repo, run
   `ollama-sentinel install-hooks --pre-commit --fail-on high`.
3. Remaining items in `CLAUDE.md`'s next-moves table are optional architectural
   work (`Finding`/`ImpactItem` unification with `research_agent` and guardrail
   candidate UI polish).
