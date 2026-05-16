# Implementation-vs-plan audit — 2026-05-15

Read-only audit of all 6 plans in `docs/superpowers/plans/` against the code
that actually landed. master @ `47f1929`, working tree clean, 494 tests
collected (479 passed / 15 skipped).

Method: 6 parallel read-only agents, one per plan. Plans CLAUDE.md marks
shipped got a claims-verification pass against `followups.md`; the two live
plans (reviewer-grounding, v0.2 incident schema) got full audits.

---

## Verdict table

| Plan | Header said | Reality | Action taken |
|---|---|---|---|
| 2026-05-09-reviewer-grounding | ready for review, then implementation | **SHIPPED** | header corrected |
| 2026-05-02-v02-incident-schema | ready for review, then implementation | **NOT STARTED (parked, clean)** | header clarified |
| 2026-05-01-phase-a-qwen3-hot-path-swap | ready to ship | **SHIPPED** (2 hardening deviations) | header corrected |
| 2026-05-01-cb3-wire-find-similar-webpages | ready to ship | **SHIPPED** | header corrected |
| 2026-04-16-context-builder | (no header) | **SHIPPED** | header added |
| 2026-04-16-triage | (no header) | **SHIPPED** (TR-3 deviation) | header added |

**No plan is partially implemented or silently abandoned.** Everything
marked shipped is real; the one plan marked parked is genuinely untouched.

---

## Headline finding — CB-1 is mis-tracked as OPEN everywhere

`followups.md` lists **CB-1 (dedupe impact-report formatters) as OPEN**, and
CLAUDE.md repeats it as **"Pickable next moves" item #1** plus in the "Open
follow-ups" / "Resume here next time" breadcrumbs.

**CB-1 was closed by commit `1313681`** ("refactor(context): dedupe
format_impact_report into shared canonical function (CB-1)"). Verified
directly:

- Canonical formatter: `ollama_sentinel/context/recipes.py:115` `format_impact_report`.
- `research_agent/tools/synthesis.py:18` imports it as `_shared_format_impact_report`.
- `synthesis.py:115-122` is now a thin wrapper delegating to the shared
  formatter (only prepends an `"IMPACT ANALYSIS: "` header). The divergence
  the ticket described (synthesis emits a SUGGESTED FIRST COMMIT block, recipe
  does not) no longer exists.

Commit `1313681` lands after CB-7 (`826648f`) and before the grounding work,
i.e. after the `followups.md` CB section was last meaningfully touched —
which is why it was missed. **Net effect: the top-of-CLAUDE.md "next move"
is already done.** This is the single highest-value correction in this audit.

---

## Per-plan detail

### 2026-05-09-reviewer-grounding — SHIPPED (high confidence)

Every Pre-1 / Step 1.1–1.7 / Step 2 / Step 3 item and all listed test
guarantees are present and green.

- Pre-1 `2a83477`; `_REVIEW_SCHEMA` + schema-constrained `generate_review`
  `810fc02`; `_validate_verbatim` + `verbatim_excerpt` on Finding + rename
  `extract_findings`→`validate_findings` `720972a`; quote-first INSTRUCTIONS
  section `d3262d6`; slop-regression suite (R1–R4) `2f1e18e`; `--no-grounding`
  `47f1929`.
- Deviations (all net-positive, documented, test-locked):
  - Regex fallback **kept behind `--no-grounding`** (`extract_findings_legacy`)
    rather than deleted — the plan explicitly permitted the flag option.
  - `--no-grounding` is a richer escape hatch than sketched: a real
    `ProcessingConfig.grounding` knob plumbed CLI→YAML→`_review_format()`→
    INSTRUCTIONS section. 8 new tests.
  - `validate_findings` is `async` (plan implied sync). Harmless; awaited at
    `watcher.py:238`.
- Minor empirical gap (not a defect): validation-checklist item 2 ("three
  known-correct real reviews round-trip un-rejected") is covered by synthetic
  positive cases, not replayed real-history fixtures.
- **Doc debt:** the plan *body* still reads as pre-implementation ("After
  Pre-1 lands…", "Ground truth … master @ `5c1e4a6`, Pre-1 not yet
  shipped"). Header is fixed; a reader of the body should know it describes
  pre-state.

### 2026-05-02-v02-incident-schema — NOT STARTED, cleanly parked (high confidence)

Zero footprint in code. No `Incident` dataclass, no `incidents` table, no
`hooks.py`/`pytest_plugin.py`, no `install-hooks`/`record-commit`/`confirm`/
`incidents` CLI verbs, no `pytest11` entry point, no tests. `grep -rni
incident ollama_sentinel/ research_agent/ tests/` → no matches. The only
"v0.2" commit (`a936c92`) is docs-only.

The plan's own "Ground truth at the time this spec was written" section still
matches reality exactly. The `Finding` dataclass gained `verbatim_excerpt`
via the *grounding* work (`720972a`) — unrelated to this plan, not partial
implementation of it. CLAUDE.md's "parked" claim is accurate. **Its
prerequisite (reviewer-grounding) is now satisfied**, so this is the next
real implementation candidate when picked up.

### 2026-05-01-phase-a-qwen3-hot-path-swap — SHIPPED (high confidence)

All 11 acceptance criteria satisfied. `EmbeddingConfig` named-role dict with
`extra="forbid"`, legacy `model:` auto-migration with one-shot v0.3
deprecation warning, hot default `qwen3-embedding:4b`. 33 tests pass for
embed/qwen/migrate.

- Two documented deviations that **harden** the spec (both test-locked,
  documented inline in `models.py:18-33,251-256`): §1 merge-in-validator
  pre-registers `consolidation`/`rerank` for partial dicts; §5
  `_KNOWN_EMBEDDING_ROLES` rejects unknown role keys (plan's `extra="forbid"`
  only covered top-level).
- `timeout_seconds` field on `EmbeddingConfig` is unrelated later work
  (`d17fd8c`, `c72d2ee`), not Phase A scope creep.
- Recall-diff receipt captured but sparse (thin violation DB) — plan's soft
  criterion #10 met formally; re-capture once the DB has real data.

### 2026-05-01-cb3-wire-find-similar-webpages — SHIPPED (high confidence)

Cleanest of the audited plans. Commit `821b6b0`. `research_agent/core/prompts.py`
leaf module with `_format_similar_pages_block`; `workflow.py:144` analyze node
calls `find_similar_webpages_sync` alongside `find_similar_queries_sync`;
renders the "Relevant pages from prior research:" block. 93 tests pass with
no `[research]` extras needed.

- Test mechanism deviated (transparently, in commit body): 5 pure formatter
  unit tests + 1 source-level wiring guard instead of the plan's 3 monkeypatch
  integration tests. The plan pre-authorized this; matches the documented
  closure-testing convention. Equivalent guarantees.

### 2026-04-16-context-builder — SHIPPED (high confidence)

CB-2..CB-7 all verified against their cited commits and current code
(`566eb67`, `aa28795`, `3b58e66`, `de45da4`, `826648f`; CB-3 `821b6b0`).
**CB-1 closed by `1313681` but tracked as OPEN — see Headline finding.** All
plan File-Structure-table deliverables present.

### 2026-04-16-triage — SHIPPED (high confidence)

TR-1 (`9ecee0a`, leaf `prompts.py`, no intra-package imports), TR-2
(`350929e`, renamed test + caplog assertion), TR-3 (real & still documented:
empty input → exit 1/ERROR, not spec's exit 0/INFO — deliberate, in
`followups.md`). All File-Structure deliverables present. 42 tests pass. One
harmless `RuntimeWarning` (un-awaited AsyncMock) in the test run.

---

## Actions applied (this audit)

1. `followups.md` CB-1 → marked DONE (commit `1313681`).
2. CLAUDE.md → CB-1 removed from "Pickable next moves" / "Open follow-ups" /
   "Resume here"; test baseline noted as 494 collected.
3. Status headers corrected on the 4 plans that have them; status line added
   to the 2 that don't (context-builder, triage).

## Resolved same day (2026-05-15)

Both reviewer-grounding flags were closed for good rather than deferred:

- **Plan-body staleness.** Not a wholesale rewrite (that would turn a
  planning doc into a duplicate of this audit). Added per-section
  `> **SHIPPED**` markers under Step 1 / Step 2 / Step 3 / Validation so a
  mid-doc reader can't be misled; the "Ground truth at the time this spec
  was written" section stays frozen by design. Header was already annotated.
- **Validation item 2 (synthetic positives).** Closed with
  `test_correct_finding_on_real_source_is_not_rejected` (P1-P4) in
  `tests/test_grounding_regression.py`. Fixtures are VERBATIM real source
  frozen from `processor.py` @ 47f1929 (the exact `@retry` construct the
  plan's schema example cites), each with a uniqueness guard so it cannot
  pass for the wrong reason. Covers the strictness modes item 2 names that
  R3 did not: multi-line spans, line-range start/end boundaries, deep
  indentation, regex-metachar excerpts. Path A (replay pre-grounding
  `.ollama_reviews/` captures) is structurally impossible — those predate
  the `verbatim_excerpt` field, so they lack the key the validator checks.
  Suite: 483 passed / 15 skipped.

## Not done (flagged, no action)

- phase-a recall-diff receipt is sparse — re-capture when violation DB has
  real data.
