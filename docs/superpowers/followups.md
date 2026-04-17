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

### CB-2. SemanticRetriever integration test

**Files:** `tests/context/test_recipes.py`.

**Issue:** recipes are tested with `NullRetriever`; `SemanticRetriever` is
tested in isolation. No test exercises the full chain
`ContextItem → SemanticRetriever.rank → assemble → build_review_context`.

**Fix:** add one parametric test using the `_FakeEmbedder` pattern from
`tests/context/test_retrievers.py`.

**Trigger:** add before the next change to `SemanticRetriever` or the
recipe signature.

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

### CB-4. Retriever identity-fallback test doesn't prove identity

**Files:** `tests/context/test_retrievers.py` —
`test_falls_back_to_identity_on_embedding_unavailable`.

**Issue:** input is `[a, b]` and expected fallback is `[a, b]`. The
assertion would also pass if the fallback returned `sorted(items)` — it
only proves a stable sort, not that original order is preserved.

**Fix:** change input to `[b, a]` and assert the fallback returns
`[b, a]`.

### CB-5. Retriever log-level split — DONE (commit 3b58e66)

**Files:** `ollama_sentinel/context/assembler.py:_render_optional_section`.

`except Exception` now logs at `ERROR` with `exc_info=True`; `EmbeddingUnavailable`
caught separately at `WARNING`. Import added. Full test suite passes.

---

### CB-6. `chunk_by_lines` oversized-line caveat undocumented

**Files:** `ollama_sentinel/context/assembler.py:chunk_by_lines`.

**Issue:** a single line longer than `max_tokens` produces one chunk that
exceeds the budget. Downstream `_render_section` truncation catches it,
but a caller reading `max_tokens` as a hard guarantee would be surprised.

**Fix:** one docstring line. No code change needed.

### CB-7. Stale dev-extra duplication in `pyproject.toml`

**Files:** `pyproject.toml`.

**Issue:** `diskcache>=5.6.0` appears in both core `dependencies` and the
`[dev]` extras block. Harmless but redundant after the TR1 promotion.

**Fix:** drop the `[dev]` duplicate.

---

## Triage (landed 2026-04-16)

Plan: `docs/superpowers/plans/2026-04-16-triage.md`.
Spec: `docs/superpowers/specs/2026-04-16-triage-design.md`.

### TR-1. `TRIAGE_SYSTEM_PROMPT` relocation (latent cycle)

**Files:** `ollama_sentinel/config.py:11`,
`ollama_sentinel/triage/runner.py`.

**Issue:** `config.py` imports `TRIAGE_SYSTEM_PROMPT` from `triage.runner`,
which drags `runner → context → recipes` into every config load
(~220ms). Any future code in `triage/` needing a config constant would
create a circular import.

**Fix:** move `TRIAGE_SYSTEM_PROMPT` to a leaf
`ollama_sentinel/triage/prompts.py`; import from there in both `config.py`
and `runner.py`.

**Trigger:** if `triage/` ever needs to import from `config`.

### TR-2. TTY-error test message assertion

**Files:** `tests/test_cli.py::test_tty_without_input_exits_with_error`.

**Issue:** the test asserts `exit_code == 1` only. A refactor that changes
the `"No input — pipe tool output or pass a path."` message to something
unhelpful would silently pass.

**Fix:** add
`assert "No input" in (result.stdout or "") + (result.output or "")`.

### TR-3. Spec deviation: empty-input exit code

**Files:** `ollama_sentinel/cli.py:triage`.

**Issue:** the spec said "empty input → exit 0 with INFO". The final
implementation is "empty input → exit 1 with ERROR" for both stdin and
file-path branches (reasoning: the user meant to pass something and
didn't — usage error). Internal consistency chosen over strict spec
adherence.

**Fix:** no action required unless the interpretation changes. Documented
here for traceability.
