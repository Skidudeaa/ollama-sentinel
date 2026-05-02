# Open Follow-ups

Deferred-but-known items from the ContextBuilder and Triage landings.
Not blockers — each entry has enough context to pick up in a fresh session.

- [ContextBuilder](#contextbuilder-landed-2026-04-16) (2026-04-16)
- [Triage](#triage-landed-2026-04-16) (2026-04-16)
- [Operational DX](#operational-dx-filed-2026-05-02) (2026-05-02)

---

## ContextBuilder (landed 2026-04-16)

Plan: `docs/superpowers/plans/2026-04-16-context-builder.md`.
Spec: `docs/superpowers/specs/2026-04-16-context-builder-design.md`.

### CB-1. Dedupe impact-report formatters

**Files:** `ollama_sentinel/context/recipes.py:_format_impact_report`,
`research_agent/tools/synthesis.py:format_impact_report`.

**Issue:** two formatters diverge — the `SynthesisTool` version emits a
`SUGGESTED FIRST COMMIT` block for HIGH-severity items; the recipe version
does not. Currently mutually exclusive (synthesis short-circuits impact
before reaching the recipe), so harmless today.

**Fix:** move the canonical formatter to a neutral location
(`ollama_sentinel/context/recipes.py` or a shared
`research_agent/core/impact.py`) and have both callers import it.

**Trigger:** any PR that makes `build_research_context` a reachable path
for impact data.

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

### OP-1. `ollama-sentinel run` doesn't hot-reload `ollama-sentinel.yaml`

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
