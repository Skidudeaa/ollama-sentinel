# _archive/

This directory holds code that was removed from active paths during the
2026-04-10 consolidation. Nothing here is imported, called, or built against.
Files are preserved (not deleted) so future readers can see what was tried
and what replaced it.

Do not add new code here. Do not import from here.

## Contents

### `ollama_sentinel_pre_memory_snapshot/`

A snapshot of the sentinel package from before the violation-memory feature
landed. Originally lived at the project root as `ollama_sentinel copy/`
(last substantive edit Aug 2025). Superseded in full by `ollama_sentinel/`
(2026-04-09 rewrite).

The snapshot was archived, not deleted, because the consolidation brief said
to preserve superseded versions. It has **zero unique logic**: every module
is a strict subset of the current `ollama_sentinel/` package and is also
strictly worse on correctness and security. Specifically:

- `models.py` uses Pydantic v1 (`@validator`) and has no `MemoryConfig`, no
  host-URL scheme validator, and no output-directory path-traversal validator.
  Replaced by `ollama_sentinel/models.py` (Pydantic v2, full validators).
- `processor.py` has no violation memory integration, no `_format_violations`,
  no `prior_violations` parameter, uses `.dict()` instead of `.model_dump()`,
  and uses a single-timeout httpx client instead of per-phase timeouts.
  Replaced by `ollama_sentinel/processor.py`.
- `utils.py` has a string-prefix path-traversal check that is vulnerable to
  sibling-directory confusion (`/safe_evil/x` matches prefix `/safe`).
  Replaced by `ollama_sentinel/utils.py`, which uses `Path.relative_to()`
  for proper containment.
- `watcher.py` does not create a `ViolationDB`, does not call
  `extract_findings`, and does not feed prior findings back into reviews.
  Replaced by `ollama_sentinel/watcher.py`.
- `cli.py` has no `report` command.
  Replaced by `ollama_sentinel/cli.py`.
- `config.py`, `__init__.py` — minor drift only; replaced by the
  current equivalents.

### `research_agent_orphans/metrics.py`

A `PerformanceTracker` / `track_time` / global `PERFORMANCE_TRACKER` module
that was never imported by any other file in the project. The research
workflow uses a separate `NullTelemetry` implementation from
`research_agent/core/agent.py` instead. Preserved in case telemetry becomes
a real requirement later.

### `research_agent_orphans/prompt.py`

A `PromptManager` with compiled Handlebars templates for `analyze`, `search`,
`code_search`, `synthesize`, and `verify`. Never imported anywhere.
`research_agent/core/workflow.py` uses inline prompt strings, and
`research_agent/tools/synthesis.py` compiles its own template. Preserved
because the `synthesize` and `verify` templates here are more detailed than
their inline counterparts and might be worth reintroducing as a separate
concern.
