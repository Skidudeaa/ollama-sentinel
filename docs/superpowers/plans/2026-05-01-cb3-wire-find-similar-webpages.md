# CB-3: Wire `find_similar_webpages_sync` into the analyze node

**Status:** ready to ship
**Effort:** ~30 min implementation + ~30 min tests, plus optional polish
**Owner:** unassigned
**Plan source:** Phase D of `~/.claude/plans/yes-putting-both-moonlit-galaxy.md`
(scoped down — Phases A/B/C explicitly deferred)

---

## Why this exists, in one paragraph

`research_agent/tools/memory.py` ships two semantic-recall sync wrappers:
`find_similar_queries_sync` and `find_similar_webpages_sync`. Both are fully
implemented. Both fall back to token-overlap when no embedder is configured.
Today the analyze node calls `find_similar_queries_sync` (good) but never
calls `find_similar_webpages_sync` (the CB-3 gap). This means every research
session re-discovers webpages it has already cached from prior sessions
because the analyze step doesn't consult them. The fix is one new call in
`workflow.py` and a small downstream prompt change so the recalled pages
actually influence the analysis. That's the whole ticket.

This is **not** the larger Qwen3 embedding migration (Phases A/B/C). Those
phases are parked pending v0.2 Incident schema work. Do **not** touch
`EmbeddingConfig`, do **not** add a reranker, do **not** introduce a new
embedding model. The goal is to close the documented wiring gap with the
substrate as it exists today.

---

## Acceptance criteria

1. The analyze node in `research_agent/core/workflow.py` calls
   `memory.find_similar_webpages_sync(session.query, limit=5)` in addition to
   the existing `find_similar_queries_sync` call.
2. The recalled pages are surfaced in the analyze prompt under a clearly
   labeled section, with each page rendered as `- {title} ({url})` (one line
   per page; truncate to a sensible single-line length).
3. Behavior with no embedder configured is unchanged from today: the sync
   wrapper transparently degrades to token-overlap inside `memory.py`. The
   workflow does **not** branch on whether an embedder exists.
4. No regressions in the existing test suite. The full suite still runs in
   under ~3s.
5. New tests:
   - one unit test that monkeypatches
     `EnhancedMemoryStore.find_similar_webpages_sync` and asserts it is
     invoked exactly once during the analyze step with the session query;
   - one unit test that confirms recalled pages appear in the prompt
     string passed to the LLM (assert via a `monkeypatch` on `llm.invoke`
     that captures the prompt argument);
   - one regression test confirming that when memory is empty,
     `find_similar_webpages_sync` returns `[]` and the analyze step still
     completes without error.
6. Followups list: mark CB-3 closed in `docs/superpowers/followups.md` with
   the merge-commit SHA.
7. Recent landings: prepend a one-line entry in `CLAUDE.md`'s
   "Recent landings" section.

Out of scope for this ticket:
- `EmbeddingConfig.models` refactor (Phase A — parked).
- Consolidation role / `report --quality high` (Phase B — parked).
- Reranker (Phase C — parked).
- Migrating workflow.py's embedder construction off the legacy single-`model`
  config key. That migration lands with Phase A. Today's call site
  (`embed_cfg.get("model", "nomic-embed-text")`) stays as-is.

---

## Current state — verified by reading the repo on this branch

`research_agent/core/workflow.py`, analyze node (around line 124):

```python
similar_queries = memory.find_similar_queries_sync(session.query)

similar_queries_text = ""
if similar_queries:
    similar_queries_text = "Similar past queries:\n" + "\n".join([
        f"- {q.text}" for q in similar_queries
    ])
```

`research_agent/tools/memory.py` (lines 217–238): both `find_similar_*_sync`
methods exist and route to `find_similar_*_semantic` when an embedder is
configured, falling back to `find_similar_*` (token-overlap) otherwise.
**No changes needed in `memory.py` for this ticket.**

---

## Implementation — exact diff

### 1. `research_agent/core/workflow.py` — analyze node

Locate the block in the `analyze` function that currently reads:

```python
            # Check for similar questions in memory (semantic when embedder available)
            similar_queries = memory.find_similar_queries_sync(session.query)

            similar_queries_text = ""
            if similar_queries:
                similar_queries_text = "Similar past queries:\n" + "\n".join([
                    f"- {q.text}" for q in similar_queries
                ])
```

Replace with:

```python
            # Check for similar questions AND prior webpages in memory.
            # Sync wrappers route to the semantic path when an embedder is
            # configured and degrade to token-overlap otherwise — see
            # research_agent/tools/memory.py for the fallback behavior.
            similar_queries = memory.find_similar_queries_sync(session.query)
            similar_pages = memory.find_similar_webpages_sync(session.query, limit=5)

            similar_queries_text = ""
            if similar_queries:
                similar_queries_text = "Similar past queries:\n" + "\n".join([
                    f"- {q.text}" for q in similar_queries
                ])

            similar_pages_text = ""
            if similar_pages:
                lines = []
                for p in similar_pages:
                    title = (p.title or "").strip()
                    url = (p.url or "").strip()
                    label = title or url or "(untitled)"
                    # Truncate the title to keep one prompt line per page.
                    if len(label) > 120:
                        label = label[:117] + "..."
                    if url and label != url:
                        lines.append(f"- {label} ({url})")
                    else:
                        lines.append(f"- {label}")
                similar_pages_text = "Relevant pages from prior research:\n" + "\n".join(lines)
```

Then, in the same function, locate the prompt assembly that currently reads:

```python
            prompt = f"""
Analyze this research query and create a plan:

QUERY: {session.query}

{similar_queries_text}

{f'CODE CONTEXT: {session.code_context}' if session.code_context else ''}

Determine:
...
"""
```

Replace with:

```python
            prompt = f"""
Analyze this research query and create a plan:

QUERY: {session.query}

{similar_queries_text}

{similar_pages_text}

{f'CODE CONTEXT: {session.code_context}' if session.code_context else ''}

Determine:
...
"""
```

(Keep the rest of the prompt body and the trailing instructions unchanged —
the diff above only inserts the `{similar_pages_text}` line.)

### 2. Tests — `tests/test_research_agent.py` (or wherever the workflow tests live)

> Conformance check before writing tests: run
> `find tests -name 'test_*workflow*' -o -name 'test_research*'`
> and put the new tests in whatever file already exercises `build_workflow`'s
> analyze node. If no such file exists, add the cases to the closest
> existing workflow-level test module. Mirror the existing fixture pattern
> for `build_workflow` so we don't reinvent the construction call.

Three new test cases. The exact construction call for the workflow has to
match how existing tests do it — copy the pattern from a sibling test rather
than synthesizing a new one. Skeletons below show the assertion shape.

```python
def test_analyze_invokes_find_similar_webpages_sync(monkeypatch):
    """CB-3: analyze must consult prior webpage neighbors via sync wrapper."""
    from research_agent.tools.memory import EnhancedMemoryStore

    captured: dict = {"calls": []}

    def fake_pages(self, query, limit=5):
        captured["calls"].append((query, limit))
        return []

    monkeypatch.setattr(
        EnhancedMemoryStore, "find_similar_webpages_sync", fake_pages
    )

    # ... build the workflow + drive analyze the same way the existing
    # analyze-node test does. After the analyze node runs:
    assert len(captured["calls"]) == 1
    query_seen, limit_seen = captured["calls"][0]
    assert query_seen  # non-empty
    assert limit_seen == 5


def test_analyze_prompt_includes_similar_pages(monkeypatch):
    """CB-3: recalled pages must appear in the prompt sent to the LLM."""
    from research_agent.tools.memory import EnhancedMemoryStore, WebPage

    fake_page = WebPage(
        url="https://example.com/x",
        title="Example Reference",
        summary="",
        content="",
    )

    def fake_pages(self, query, limit=5):
        return [fake_page]

    monkeypatch.setattr(
        EnhancedMemoryStore, "find_similar_webpages_sync", fake_pages
    )

    captured_prompts: list = []

    # Patch the LLM's invoke to capture the prompt argument and return a stub.
    # The exact patch target depends on how the workflow accesses the LLM —
    # mirror whatever the existing analyze-node test patches.
    # ...

    # After analyze runs:
    assert any(
        "Relevant pages from prior research" in p
        and "Example Reference" in p
        and "https://example.com/x" in p
        for p in captured_prompts
    )


def test_analyze_handles_empty_memory(monkeypatch):
    """CB-3 regression: empty memory must not crash analyze."""
    from research_agent.tools.memory import EnhancedMemoryStore

    monkeypatch.setattr(
        EnhancedMemoryStore,
        "find_similar_webpages_sync",
        lambda self, query, limit=5: [],
    )
    monkeypatch.setattr(
        EnhancedMemoryStore,
        "find_similar_queries_sync",
        lambda self, query, limit=3: [],
    )

    # ... drive analyze, assert no exception, session.get_step("analyze").output
    # is non-empty.
```

Use the existing test module's construction fixture for `build_workflow` and
the existing pattern for patching `llm.invoke`. If neither exists, the
analyze node currently isn't unit-tested at all — flag that in the PR
description and note that this ticket is the first analyze-node coverage.

### 3. Followups — `docs/superpowers/followups.md`

Locate the CB-3 entry and mark it closed:

```markdown
- [x] CB-3 — wire `find_similar_webpages_sync` from analyze node
      Closed YYYY-MM-DD by <merge-commit-sha>. Phases A/B/C of the Qwen3
      embedding plan remain parked pending v0.2 Incident schema.
```

### 4. Recent landings — `CLAUDE.md`

At the top of the "Recent landings" section, prepend:

```markdown
- YYYY-MM-DD: CB-3 closed. `research_agent`'s analyze node now consults
  prior webpage neighbors via `find_similar_webpages_sync`, alongside the
  existing `find_similar_queries_sync` call. No new dependencies; sync
  wrapper degrades to token-overlap when no embedder is configured.
```

(Replace `YYYY-MM-DD` with the merge date.)

### 5. CLAUDE.md "Pickable next moves" — remove the CB-3 row

Delete the row in the table that references CB-3.

---

## Verification — run before opening the PR

```bash
# Full suite, must stay green.
pytest tests/ -v

# The three new tests should be visible.
pytest tests/ -v -k "find_similar_webpages_sync or similar_pages or empty_memory"

# Smoke test against live Ollama (optional but recommended). Pre-seeds a
# webpage, then runs a query that should retrieve it. The "Relevant pages
# from prior research" line should appear in the analyze step output.
python -m research_agent.main query "test query" --output /tmp/cb3.md
grep -i "Relevant pages from prior research" /tmp/cb3.md || echo "no recall this run (expected if memory is empty)"
```

The grep is a soft check — first-run memory is empty and the line legitimately
won't appear. Run a second query in the same session to confirm the recall
fires.

---

## Things that look like the ticket but aren't

- **Don't refactor the embedder construction in workflow.py.** The line
  `embed_cfg.get("model", "nomic-embed-text")` stays for now. Phase A of
  the parked plan migrates this to `embed_cfg["models"]["hot"]` with a
  back-compat helper. Mixing that in here couples a one-line wiring fix
  to a config refactor and inflates the blast radius.
- **Don't add an embedder where none exists.** If a user runs without
  `embedding.enabled = true`, the sync wrapper falls back to token-overlap.
  That's the documented behavior and we want to preserve it.
- **Don't change `find_similar_webpages_sync`'s signature or semantics.**
  Memory.py is correct. This is a workflow.py-only change plus tests.
- **Don't promote `ImportResolver` or anything else into shared
  infrastructure as part of this ticket.** That's a v0.3 move documented in
  `docs/VISION.md`.

---

## PR description template

```
CB-3: wire find_similar_webpages_sync into research_agent analyze node

The analyze node already consulted similar past queries via
find_similar_queries_sync but never queried prior webpage neighbors,
even though find_similar_webpages_sync exists and is implemented. This
PR closes the wiring gap.

Behavior:
- Adds a single call to find_similar_webpages_sync(session.query, limit=5)
  alongside the existing query-similarity call.
- Renders the recalled pages into a "Relevant pages from prior research"
  block in the analyze prompt.
- Sync wrapper transparently degrades to token-overlap when no embedder
  is configured — no behavioral change for users without embedding enabled.

Out of scope:
- Qwen3 embedding migration (Phases A/B/C of
  ~/.claude/plans/yes-putting-both-moonlit-galaxy.md). Parked pending v0.2
  Incident schema; tracked in docs/superpowers/followups.md.

Tests: 3 new (invocation, prompt-content, empty-memory regression).
Total suite: <N+3> passing, 15 skipped, ~<runtime>s.
```

---

## Ground truth at the time this spec was written

Repo at HEAD:
- `research_agent/tools/memory.py` lines 217–238: both `find_similar_*_sync`
  methods present and correct.
- `research_agent/core/workflow.py` line 124: `find_similar_queries_sync`
  wired in analyze node; `find_similar_webpages_sync` not called anywhere
  in the file.
- `research_agent/core/workflow.py` lines 70–83: embedder constructed from
  legacy `embed_cfg.get("model", "nomic-embed-text")`. Untouched by this
  ticket.

If any of the above has changed by the time someone picks this up,
re-confirm the current state before applying the diff.
