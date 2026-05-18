# Grounding graceful-degrade — fall back to legacy extractor when the model ignores `format`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When grounded review JSON fails to parse, extract findings from the prose with the legacy regex extractor instead of silently dropping all findings.

**Architecture:** Add one parse-failure signal to `_parse_review_response`, expose the fallback decision as a pure predicate, and widen the watcher's legacy-extraction branch to honor that signal. No new dependencies, no schema changes.

**Tech Stack:** Python 3.10+, pytest (`asyncio_mode=auto`), `pytest-httpx`.

**Status:** ready for implementation
**Effort:** ~45 min
**Prerequisites:** none
**Follow-up to:** [`docs/superpowers/plans/2026-05-09-reviewer-grounding.md`](2026-05-09-reviewer-grounding.md)

---

## The problem in one paragraph

`grounding: true` (default) makes `FileProcessor._parse_review_response` do
`json.loads(raw)` against `_REVIEW_SCHEMA`. Models that don't grammar-enforce
Ollama's `format` parameter — every `:cloud` model, and any local model whose
`system_prompt` asks for markdown — return prose, not JSON. `json.loads("## …")`
raises `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`, which is
logged at **ERROR** and the review is returned as
`{"summary": <prose>, "findings": []}`. Back in `watcher._process_file`, the
legacy regex extractor only runs `elif not self.config.processing.grounding:`
— so under grounding it never runs, and **every review for such a model
persists zero findings**, silently killing violation memory / semantic recall.
Confirmed by live reproduction against `deepseek-v4-pro:cloud` on
`EnhancedVinylPlayerView.swift` (2026-05-17): 200 OK, ~6.8 KB of correct
markdown, `json.loads` fails at char 0, findings dropped.

## Design

Two surgical changes plus one extracted predicate:

1. `_parse_review_response` adds `"grounding_parse_failed": True` to its return
   dict **only** in the `except (JSONDecodeError, TypeError, ValueError)`
   branch, and downgrades the log from `error` to `warning` (this is a
   handled, recoverable degrade — same severity class as the existing
   verbatim-rejection WARNING from commit `dc26f6c`). Valid JSON with
   `findings: []` is unaffected — the flag is **not** set, no log emitted.
2. A pure module-level predicate `_should_run_legacy_extractor(grounding,
   review)` in `ollama_sentinel/watcher.py` returns `True` when grounding is
   off **or** the review carries `grounding_parse_failed`. This isolates the
   testable logic (confirmed unit mechanism) from the hard-to-exercise
   `_process_file` wiring (guarded by a source grep — the documented
   closure-testing pattern).
3. `watcher._process_file` replaces the inline `elif not
   self.config.processing.grounding:` condition with a call to the predicate.

The legacy-extracted findings continue to flow **only to `ViolationDB`**
(not into `review["findings"]` / `save_review`), exactly matching today's
ungrounded behavior — no change to saved-review output.

## Out of scope

- Injecting JSON/schema instructions into the prompt (separate lever; the
  degrade makes it non-urgent).
- Changing `_REVIEW_SCHEMA`, `validate_findings`, or verbatim validation.
- Auto-detecting `:cloud` models or rewriting user `system_prompt`s.
- Config hot-reload (tracked separately in `followups.md`).
- Surfacing degraded findings in the saved markdown/JSON review file.

---

## File structure

| File | Responsibility | Change |
|------|----------------|--------|
| `ollama_sentinel/processor.py` | `_parse_review_response` | add flag on parse failure; `log.error`→`log.warning` |
| `ollama_sentinel/watcher.py` | `_should_run_legacy_extractor` (new, module-level) + `_process_file` wiring | new pure predicate; one-line condition swap |
| `tests/test_extractor.py` | `_parse_review_response` behavior | update 1 existing test; add 2 |
| `tests/test_watcher.py` | predicate + wiring guard | add predicate unit tests + source grep guard |
| `docs/superpowers/followups.md` | close-out note | append resolution entry |

---

## Task 1: Signal parse-failure from `_parse_review_response`

**Files:**
- Modify: `ollama_sentinel/processor.py:498-516`
- Modify (existing test, behavior change): `tests/test_extractor.py:363-391`
- Test: `tests/test_extractor.py` (class `TestParseReviewResponse`)

- [ ] **Step 1: Update the existing parse-failure test to the new contract**

The current test `test_schema_parse_failure_falls_back_to_prose`
(`tests/test_extractor.py:363-391`) asserts an **ERROR** log. The fix
deliberately changes this to **WARNING** + a new flag. Replace its
post-call assertions (lines 386-391) with:

```python
        assert result["summary"] == "This is free-form prose review."
        assert result["findings"] == []
        # Parse failure is now a recoverable degrade, not an error.
        assert result["grounding_parse_failed"] is True

        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("review did not parse as json" in m.lower() for m in warnings)
        assert not [r for r in caplog.records if r.levelname == "ERROR"]
```

> **Execution correction (2026-05-17):** the original plan asserted the
> substring with mismatched casing vs. the `log.warning` sentence case.
> The log message keeps correct sentence case (`"Grounded review …"`);
> the assertion is case-insensitive on the stable substring instead.

- [ ] **Step 2: Add a test that valid-JSON-empty does NOT set the flag**

Append to class `TestParseReviewResponse` in `tests/test_extractor.py`:

```python
    async def test_valid_json_empty_findings_has_no_parse_failed_flag(
        self, sentinel_config, tmp_path, httpx_mock, caplog
    ):
        """Valid JSON with findings: [] must NOT set grounding_parse_failed."""
        from ollama_sentinel.processor import FileProcessor, FileChange
        from watchfiles import Change
        import json

        source = tmp_path / "clean.py"
        source.write_text("print('ok')\n")
        httpx_mock.add_response(
            url="http://localhost:11434/api/chat",
            json={"message": {"content": json.dumps(
                {"summary": "Clean.", "findings": []})}},
        )
        fp = FileProcessor(sentinel_config)
        fc = FileChange(path=source, change_type=Change.modified)
        try:
            result = await fp.generate_review(fc)
        finally:
            await fp.ollama_client.close()

        assert result["findings"] == []
        assert "grounding_parse_failed" not in result
        assert not [r for r in caplog.records
                    if r.levelname in ("WARNING", "ERROR")]
```

- [ ] **Step 3: Run both tests, verify they FAIL**

Run: `python -m pytest tests/test_extractor.py::TestParseReviewResponse -v`
Expected: `test_schema_parse_failure_falls_back_to_prose` FAILS (still ERROR /
no flag); `test_valid_json_empty_findings_has_no_parse_failed_flag` PASSES
(flag already absent, but keep it — it locks the no-regression guarantee).

- [ ] **Step 4: Apply the implementation change**

In `ollama_sentinel/processor.py`, replace the `except` block of
`_parse_review_response` (currently lines 514-516):

```python
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            log.error("Schema validation failed for review response: %s", e)
        return {"summary": raw, "findings": []}
```

with:

```python
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            # The model ignored Ollama's `format` schema (common for :cloud
            # models and markdown-instructed system prompts) and returned
            # prose. Recoverable: the watcher degrades to the legacy regex
            # extractor on the prose. WARNING, not ERROR — handled path.
            log.warning(
                "Grounded review did not parse as JSON (%s); "
                "degrading to legacy prose extractor", e,
            )
            return {"summary": raw, "findings": [], "grounding_parse_failed": True}
        return {"summary": raw, "findings": []}
```

Note: the log substring is lower-cased in the message
(`"grounded review did not parse as JSON"`); the test matches that exact
casing.

- [ ] **Step 5: Run the full extractor test module, verify GREEN**

Run: `python -m pytest tests/test_extractor.py -v`
Expected: all PASS, including `test_empty_findings_produces_prose_no_findings`
(unchanged — valid JSON path never sets the flag, emits no WARNING).

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/processor.py tests/test_extractor.py
git commit -m "fix(grounding): flag JSON parse failure as recoverable degrade

_parse_review_response now returns grounding_parse_failed=True and logs
at WARNING (not ERROR) when the model ignores the format schema and
returns prose. Lets the watcher degrade to the legacy extractor instead
of silently dropping all findings. Follow-up to 2026-05-09 grounding."
```

---

## Task 2: Degrade to legacy extractor in the watcher

**Files:**
- Modify: `ollama_sentinel/watcher.py` (add module-level predicate; rewire `_process_file:241`)
- Test: `tests/test_watcher.py`

- [ ] **Step 1: Write predicate unit tests (failing)**

Append to `tests/test_watcher.py`:

```python
class TestShouldRunLegacyExtractor:
    """Pure predicate: when does the legacy regex extractor run?"""

    def test_ungrounded_always_runs(self):
        from ollama_sentinel.watcher import _should_run_legacy_extractor
        assert _should_run_legacy_extractor(False, {"summary": "x"}) is True

    def test_grounded_clean_parse_does_not_run(self):
        from ollama_sentinel.watcher import _should_run_legacy_extractor
        assert _should_run_legacy_extractor(True, {"summary": "x"}) is False

    def test_grounded_parse_failure_runs(self):
        from ollama_sentinel.watcher import _should_run_legacy_extractor
        review = {"summary": "## prose", "findings": [],
                  "grounding_parse_failed": True}
        assert _should_run_legacy_extractor(True, review) is True

    def test_wiring_calls_predicate(self):
        """Source guard: _process_file must route through the predicate,
        not an inline grounding-only check (closure-testing pattern)."""
        import inspect, ollama_sentinel.watcher as w
        src = inspect.getsource(w.FileSentinel._process_file)
        assert "_should_run_legacy_extractor(" in src
        assert "elif not self.config.processing.grounding:" not in src
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/test_watcher.py::TestShouldRunLegacyExtractor -v`
Expected: FAIL — `_should_run_legacy_extractor` does not exist.

- [ ] **Step 3: Add the pure predicate**

In `ollama_sentinel/watcher.py`, add at module level (after imports, before
the `FileSentinel` class):

```python
def _should_run_legacy_extractor(grounding: bool, review: dict) -> bool:
    """Decide whether the legacy regex finding extractor should run.

    Runs when grounding is off (model emits free-form prose by design) OR
    when a grounded review failed JSON parse (`grounding_parse_failed`),
    so a model that ignored Ollama's `format` schema still yields findings
    instead of silently persisting none.
    """
    if not grounding:
        return True
    return bool(review.get("grounding_parse_failed"))
```

- [ ] **Step 4: Rewire `_process_file`**

In `ollama_sentinel/watcher.py:241`, replace:

```python
                    elif not self.config.processing.grounding:
                        # Ungrounded path: regex-extract findings from free-form prose.
                        summary_text = review.get("summary", "")
                        valid_findings = extract_findings_legacy(summary_text, str(rel_path))
```

with:

```python
                    elif _should_run_legacy_extractor(
                        self.config.processing.grounding, review,
                    ):
                        # Ungrounded by config, OR grounded but the model
                        # ignored the schema and returned prose — regex-extract
                        # findings from the free-form text instead of dropping
                        # them.
                        summary_text = review.get("summary", "")
                        valid_findings = extract_findings_legacy(summary_text, str(rel_path))
```

- [ ] **Step 5: Run predicate + watcher module, verify GREEN**

Run: `python -m pytest tests/test_watcher.py -v`
Expected: all PASS, including the existing `grounding_override` tests.

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/watcher.py tests/test_watcher.py
git commit -m "fix(grounding): degrade to legacy extractor on parse failure

_process_file now routes the legacy-extractor decision through the pure
_should_run_legacy_extractor predicate, which also fires when a grounded
review failed JSON parse. Models that ignore Ollama's format schema now
still yield persisted findings. Closes the silent-zero-findings path."
```

---

## Task 3: Full suite, follow-up close-out, PR

- [ ] **Step 1: Run the entire suite**

Run: `python -m pytest tests/ -q`
Expected: `484 passed, 15 skipped` (479 baseline + 5 net-new: 1 in Task 1
Step 2, 4 in the Task 2 `TestShouldRunLegacyExtractor` class;
`test_schema_parse_failure_falls_back_to_prose` was modified, not added).
If the count differs, investigate before proceeding — do not adjust the
assertion to match.

- [ ] **Step 2: Append the close-out to `followups.md`**

Append to `docs/superpowers/followups.md`:

```markdown
### RESOLVED 2026-05-17 — grounding silent-zero-findings on schema-ignoring models

**Files:** `ollama_sentinel/processor.py` (_parse_review_response),
`ollama_sentinel/watcher.py` (_should_run_legacy_extractor + _process_file).

**Was:** grounded reviews from models that ignore Ollama's `format`
schema (all `:cloud` models; markdown-instructed system prompts) failed
`json.loads`, logged ERROR, and persisted zero findings — violation
memory silently dead. Reproduced live against `deepseek-v4-pro:cloud`.

**Fix:** parse failure now flags `grounding_parse_failed` + logs WARNING;
watcher degrades to `extract_findings_legacy` on the prose via the pure
`_should_run_legacy_extractor` predicate. Plan:
`docs/superpowers/plans/2026-05-17-grounding-graceful-degrade.md`.

**Residual:** prompt-level JSON instruction injection still unaddressed
(out of scope here; lower priority now that degrade exists).
```

- [ ] **Step 3: Commit the doc**

```bash
git add docs/superpowers/followups.md
git commit -m "docs(followups): close grounding silent-zero-findings"
```

- [ ] **Step 4: Open the PR (one plan-piece, its own branch — PR hygiene)**

```bash
git push -u origin HEAD
gh pr create --title "fix(grounding): graceful degrade when model ignores format schema" \
  --body "Follow-up to 2026-05-09 reviewer-grounding. When a model ignores Ollama's \`format\` JSON schema (all \`:cloud\` models, markdown-instructed prompts) the grounded path failed \`json.loads\`, logged ERROR, and persisted **zero** findings — violation memory silently dead. Reproduced live against \`deepseek-v4-pro:cloud\`/\`EnhancedVinylPlayerView.swift\` on 2026-05-17.

Parse failure now flags \`grounding_parse_failed\` + WARNING; the watcher degrades to the legacy regex extractor via the pure \`_should_run_legacy_extractor\` predicate. No schema/dependency changes. Saved-review output unchanged. Plan: \`docs/superpowers/plans/2026-05-17-grounding-graceful-degrade.md\`."
```

---

## Self-review

- **Spec coverage:** Design point 1 → Task 1. Point 2 (predicate) → Task 2
  Steps 1-3. Point 3 (wiring) → Task 2 Steps 4, plus source-grep guard in
  Step 1. Out-of-scope items carry no tasks (correct). ✓
- **Placeholder scan:** every code/test step shows full literal content; no
  TBD/“handle errors”/“similar to”. ✓
- **Type/name consistency:** `_should_run_legacy_extractor(grounding: bool,
  review: dict) -> bool` and the `grounding_parse_failed` key are spelled
  identically across Tasks 1-2 and tests. Log substring
  `"grounded review did not parse as JSON"` matches the asserted casing in
  Task 1 Step 1. ✓
- **Mechanism confidence:** Task 1 uses the confirmed `httpx_mock` +
  `generate_review` pattern (`tests/test_extractor.py:291-353`). Task 2 tests
  a pure function + a source grep — both confirmed-trivial mechanisms; no
  unverified `_process_file` integration harness is assumed. ✓
