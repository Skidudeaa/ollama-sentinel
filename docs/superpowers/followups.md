# Open Follow-ups

Deferred-but-known items from the ContextBuilder and Triage landings.
Not blockers — each entry has enough context to pick up in a fresh session.

> **Project parked 2026-08-25.** Nothing below is being worked on. The items
> here are historical engineering debt, and they are **not** the top of the
> queue any more — a 17-day trial on six live projects surfaced three defects
> that outrank everything in this file. Those live in the
> [next-moves table](../../CLAUDE.md) with file:line pointers, and the
> reasoning is in the
> [verdict](../session-notes/2026-08-25-bake-verdict-and-fleet-shutdown.md).
> Read that first; then this.

- [Trial-surfaced defects](#trial-surfaced-defects-2026-08-25) (2026-08-25) — **highest priority**
- [ContextBuilder](#contextbuilder-landed-2026-04-16) (2026-04-16)
- [Triage](#triage-landed-2026-04-16) (2026-04-16)
- [Operational DX](#operational-dx-filed-2026-05-02) (2026-05-02)
- [Grounding graceful-degrade](#grounding-graceful-degrade-resolved-2026-05-17) (resolved 2026-05-17)

---

## Trial-surfaced defects (2026-08-25 — RESOLVED 2026-11-02)

Found by running the tool against six real projects, not by reading the code.
All four items below were resolved on 2026-11-02 (`0088785`). Full measurements in the verdict note.

### TD-1. Severity is keyword-scanned over quoted code — DONE (2026-11-02, commit `0088785`)

`ollama_sentinel/extractor.py` previously substring-matched `_SEVERITY_KEYWORDS`
against the whole finding block, code fence included, with `"critical"` first
in dict order. A CSS `.critical` class or `{ critical_high: 'HH' }` promoted a
style nit to critical (**125/177 (71%)** of one project's critical tier, 7/14
in another), and `_CATEGORY_KEYWORDS` had the identical flaw.

**Resolution:** `_strip_code_spans` now strips fenced and inline code before
keyword scanning; explicit `Severity:` and `Category:` labels take precedence;
negated phrases (`not critical`) are stripped before severity scanning; and
non-issue praise blocks are dropped.

### TD-2. Legacy extraction never captures `verbatim_excerpt` — DONE (2026-11-02, commit `0088785`)

`extract_findings_legacy` previously returned findings with an empty excerpt,
causing `sarif.relocate_finding` (`sarif.py:88`) to return `"stored"` and
leaving `fix`, `prune`, and `surface` relocation inert on `:cloud` models.

**Resolution:** `extract_findings_legacy` now synthesizes `verbatim_excerpt`
from `file_content` via `synthesize_excerpt`, `ViolationDB.persist_findings`
backfills empty `verbatim_excerpt` on upsert, `validate_findings` strips
accidental prompt `N: ` or diff `+` prefixes while preserving indentation, and
`FileProcessor.prepare_file_content` always populates `file_change.content`
even when `git_diff_mode: true` is active.

### TD-3. Watch scope admits non-source files — DONE (2026-11-02, commit `0088785`)

One project spent model calls reviewing `events_2026-08-11.jsonl` and
analytics `.md` (**1,702 of 14,267 findings (12%)** on non-source files).

**Resolution:** Expanded `_BUILTIN_IGNORE_PATTERNS` in `ollama_sentinel/watcher.py`
and default `ignore_patterns` in `ollama_sentinel/config.py` to ignore `.jsonl`,
`.csv`, `.tsv`, `.svg`, `.map`, `.min.js`, `.min.css`, `.worktrees/`,
`.playwright-cli/`, `.design-handoff/`, and `.aider*`.

### TD-4. `persist_findings` upsert key drifts — DONE (2026-11-02, commit `0088785`)

Keys on `(file_path, line_start, line_end, category)`. When lines shifted across
edits, re-reviews inserted duplicate rows instead of updating the existing open
finding.

**Resolution:** `FileSentinel.process_change` now calls
`reconcile_file_findings(db, rel_path, content)` before generating a new review,
auto-closing vanished findings (`resolution='stale'`) and updating shifted line
spans in place via `ViolationDB.update_finding_location` (merging any duplicate
unresolved row and repointing incidents).

---

## ContextBuilder (landed 2026-04-16)

Plan: `docs/superpowers/plans/2026-04-16-context-builder.md`.
Spec: `docs/superpowers/specs/2026-04-16-context-builder-design.md`.

### CB-1. Dedupe impact-report formatters — DONE (verified 2026-06-07)

**Files:** `ollama_sentinel/context/recipes.py:format_impact_report` (canonical),
`research_agent/tools/synthesis.py:format_impact_report` (thin wrapper).

**Was:** two formatters diverged — the `SynthesisTool` version emitted a
`SUGGESTED FIRST COMMIT` block for HIGH-severity items; the recipe version did
not.

**Resolution:** the canonical formatter now lives in
`ollama_sentinel/context/recipes.py:format_impact_report` with the
`SUGGESTED FIRST COMMIT` block reconciled in (line ~156).
`SynthesisTool.format_impact_report` imports it (`_shared_format_impact_report`)
and only prepends the standalone `IMPACT ANALYSIS:` header. No duplicate
formatter body remains; `tests/test_research_agent.py::TestImpactSynthesisFormat`
exercises the shared formatter directly. No further action required.

### CB-2. SemanticRetriever integration test — DONE (commit 566eb67)

**Files:** `tests/context/test_recipes.py`.

Added `test_semantic_retriever_ranks_violations_by_similarity` using a
`_FakeEmbedder` that drives two violations to opposite cosine scores.
Verifies the full chain from violations → ContextItem → SemanticRetriever
→ assemble → build_review_context.

### CB-3. `EnhancedMemoryStore` semantic ranking (Phase 9) — DONE (commit 821b6b0)

**Files:** `research_agent/core/prompts.py` (new), `research_agent/core/workflow.py`,
`tests/test_research_agent.py`.

The async `find_similar_*_semantic` methods and the `find_similar_*_sync`
wrappers in `research_agent/tools/memory.py:189-248` were built earlier;
this ticket closed the remaining gap by wiring `find_similar_webpages_sync`
from `workflow.py`'s `analyze` node alongside the existing
`find_similar_queries_sync` call. The recalled pages render into a
"Relevant pages from prior research:" block in the analyze prompt via
the new pure `_format_similar_pages_block` helper in `prompts.py`. The
helper sits in a leaf module so the formatter stays testable in
environments without the `[research]` extras.

Spec: `docs/superpowers/plans/2026-05-01-cb3-wire-find-similar-webpages.md`.
Phases A/B/C of the broader Qwen3 embedding plan
(`~/.claude/plans/yes-putting-both-moonlit-galaxy.md`) remain parked
pending v0.2 Incident schema.

### CB-4. Retriever identity-fallback test doesn't prove identity — DONE (commit aa28795)

**Files:** `tests/context/test_retrievers.py` —
`test_falls_back_to_identity_on_embedding_unavailable`.

Input flipped to `[b, a]`, assertion updated to require `[b, a]` output.
Now distinguishes identity preservation from a stable sort by key.

### CB-5. Retriever log-level split — DONE (commit 3b58e66)

**Files:** `ollama_sentinel/context/assembler.py:_render_optional_section`.

`except Exception` now logs at `ERROR` with `exc_info=True`; `EmbeddingUnavailable`
caught separately at `WARNING`. Import added. Full test suite passes.

---

### CB-6. `chunk_by_lines` oversized-line caveat undocumented — DONE (commit de45da4)

**Files:** `ollama_sentinel/context/assembler.py:chunk_by_lines`.

Docstring now notes that a single line longer than `max_tokens` is
emitted as one oversized chunk; downstream `_render_section`
truncation handles the overflow.

### CB-7. Stale dev-extra duplication in `pyproject.toml` — DONE (commit 826648f)

**Files:** `pyproject.toml`.

`diskcache>=5.6.0` removed from `[dev]` (already in core deps).
`toml>=0.10.2` retained in `[dev]` because it's not in core.

---

## Triage (landed 2026-04-16)

Plan: `docs/superpowers/plans/2026-04-16-triage.md`.
Spec: `docs/superpowers/specs/2026-04-16-triage-design.md`.

### TR-1. `TRIAGE_SYSTEM_PROMPT` relocation (latent cycle) — DONE (commit 9ecee0a)

**Files:** `ollama_sentinel/triage/prompts.py` (new), `triage/runner.py`,
`tests/triage/test_runner.py`.

Moved `TRIAGE_SYSTEM_PROMPT` to `prompts.py` (no intra-package imports).
`runner.py` and the test now import from there. Removes the
runner→context→recipes chain from callers that only need the constant.

### TR-2. TTY-error test message assertion — DONE (commit 350929e)

**Files:** `tests/test_cli.py::test_empty_input_exits_with_error` (renamed).

Investigation revealed the original test never reached the TTY-true
branch (line 287, "No input — pipe tool output or pass a path.") — Click's
CliRunner replaces `sys.stdin` with its own BytesIO before `invoke` runs,
defeating the `patch.object(sys.stdin, "isatty", ...)` call. The test
was always exercising the empty-input branch (line 292, "Empty input;
nothing to triage."). Renamed accordingly, swapped the doomed patch
for a `caplog` assertion that pins the guidance text. Reaching the
TTY-true branch through CliRunner needs a deeper refactor of cli.triage
stdin handling and is not addressed here.

### TR-3. Spec deviation: empty-input exit code

**Files:** `ollama_sentinel/cli.py:triage`.

**Issue:** the spec said "empty input → exit 0 with INFO". The final
implementation is "empty input → exit 1 with ERROR" for both stdin and
file-path branches (reasoning: the user meant to pass something and
didn't — usage error). Internal consistency chosen over strict spec
adherence.

**Fix:** no action required unless the interpretation changes. Documented
here for traceability.

---

## Operational DX (filed 2026-05-02)

### OP-1. `ollama-sentinel run` doesn't hot-reload — RESOLVED 2026-06-07

**Resolution:** `ollama-sentinel run` now installs a SIGHUP handler
(`FileSentinel.install_reload_handler`, POSIX-only, registered in
`FileSentinel.run`). On `kill -HUP <pid>` it calls `FileSentinel.reload_config`,
which reloads the YAML and applies model/timeout changes in place via
`FileProcessor.apply_ollama_config` (closes the old `OllamaClient`, builds a
new one, recomputes the token budget) — the `awatch` loop keeps running. A
changed `watch.directory` is warned-and-skipped (restart required); a broken
YAML is logged and the running config is left intact. Six tests in
`tests/test_hot_reload.py`. Original surface below for context.

---

### OP-1 (original). `ollama-sentinel run` doesn't hot-reload `ollama-sentinel.yaml`

**Files:** `ollama_sentinel/watcher.py:103-126` (FileSentinel.__init__),
`ollama_sentinel/processor.py:36-58` (OllamaClient.__init__ — bakes
`request_timeout` into `httpx.AsyncClient`), `ollama_sentinel/cli.py:run`.

**Issue:** YAML is loaded once at process start. Editing the file while
`ollama-sentinel run` is in flight has no effect — the user must Ctrl-C
and re-run. Discovered while bumping `request_timeout: 180 → 600` to
accommodate `deepseek-v4-pro:cloud` round-trips on chunked Swift files;
the running watcher kept timing out at 180s until restarted. Cost the
user a confused round-trip.

**Fix:** install a `SIGHUP` handler in `cli.py:run` that calls
`load_config(self.config_path)` and rebuilds `FileProcessor`'s
`OllamaClient` (and any other config-derived clients) in place. The
watcher loop itself should keep running — only the per-request config
needs rebuilding. Alternative (heavier, more robust): use `awatch` on
the YAML path and trigger the same reload on file modification.

**Trigger:** any time someone tweaks the YAML on a long-running watcher
and is surprised that nothing changed.

**Out of scope:** reloading `watch.directory` (would require restarting
the `awatch` loop, not just rebuilding clients) — first pass should
explicitly warn-and-skip directory changes and only honor model/timeout
updates.

---

## Grounding graceful-degrade (resolved 2026-05-17)

### RESOLVED 2026-05-17 — grounding silent-zero-findings on schema-ignoring models

**Files:** `ollama_sentinel/processor.py` (`_parse_review_response`),
`ollama_sentinel/watcher.py` (`_should_run_legacy_extractor` +
`FileSentinel.process_change`).

**Was:** grounded reviews from models that ignore Ollama's `format`
schema (all `:cloud` models; markdown-instructed system prompts)
persisted zero findings — violation memory silently dead — via three
distinct non-conformant outputs: JSONDecodeError on markdown (logged
ERROR), valid-JSON-but-no-`findings` (the shape `deepseek-v4-pro:cloud`
emits on short prompts), and valid non-dict JSON. Reproduced live
against `deepseek-v4-pro:cloud` on `EnhancedVinylPlayerView.swift`.

**Fix:** every non-conformant grounded output now flags
`grounding_parse_failed` (parse failure also logs WARNING, not ERROR);
under grounding only the fully schema-conformant response skips the
flag. The watcher degrades to `extract_findings_legacy` on the prose
via the pure `_should_run_legacy_extractor` predicate. Commits
`9b239a1`, `4482b02`, + path-B/D follow-up. Plan:
`docs/superpowers/plans/2026-05-17-grounding-graceful-degrade.md`.

**Residual:** prompt-level JSON instruction injection still unaddressed
(out of scope here; lower priority now that the degrade exists).
