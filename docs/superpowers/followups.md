# Open Follow-ups

Deferred-but-known items from the ContextBuilder and Triage landings.
Not blockers — each entry has enough context to pick up in a fresh session.

- [ContextBuilder](#contextbuilder-landed-2026-04-16) (2026-04-16)
- [Triage](#triage-landed-2026-04-16) (2026-04-16)

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

### CB-3. `EnhancedMemoryStore` semantic ranking (Phase 9)

**Files:** `research_agent/tools/memory.py`,
`research_agent/core/workflow.py`.

**Issue:** `find_similar_webpages` / `find_similar_queries` still use
token-overlap scoring. `ViolationDB` got semantic recall; this is the
remaining token-overlap caller.

**Fix:** add async `find_similar_*_semantic` methods backed by
`SemanticRetriever`; wire them from `workflow.py`'s `analyze` node using
the existing `asyncio.new_event_loop` pattern. Keep sync methods as
fallback.

**Trigger:** when research-agent output quality traces back to the
`analyze` node's similar-query recall.

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
