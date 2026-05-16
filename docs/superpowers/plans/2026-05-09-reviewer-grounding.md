# Reviewer grounding — schema-constrained output + verbatim validator

**Status:** SHIPPED — master @ 47f1929 (Pre-1 2a83477; Step 1 810fc02+720972a; Step 2 d3262d6; Step 3 2f1e18e; --no-grounding 47f1929). Regex fallback retained behind --no-grounding per the plan's flag option. NOTE: the body is the original pre-build plan ("After Pre-1 lands…", ground-truth @ 5c1e4a6); each Step/Validation heading carries a `> **SHIPPED**` marker recording what landed. Audit: docs/superpowers/plans/2026-05-15-implementation-audit.md
**Effort:** ~1.5 days (Step 1) + ~2h (Step 2) on top of Pre-1
**Owner:** unassigned
**Prerequisites:** Pre-1 (widen `OllamaClient.response_format` to `Optional[Union[str, dict]]`)
**Upstream of:** v0.2 incident memory (Piece 1+) — without grounding,
incidents corroborate noise rather than signal

---

## The problem in one paragraph

The reviewer model emits AI-review boilerplate (magic-numbers-into-enum,
redundant-computation, ZStack-simplification) regardless of whether
those issues exist in the file under review, and occasionally cites
stale numeric values that don't match disk content. This was flagged in
[`docs/retros/2026-05-03-config-and-timeout-debugging.md:101-109`](../../retros/2026-05-03-config-and-timeout-debugging.md)
and confirmed by the
[2026-05-09 R&D synthesis](../research/2026-05-09-v02-incident-memory-research.md)
as the upstream issue blocking v0.2's incident memory: a memory built on
ungrounded findings corroborates the model's confabulations, not real
codebase signal. Fix grounding first.

---

## The architectural decision (locked 2026-05-09)

Three options for where grounding lives were considered:

1. **`generate_review` emits structured JSON+prose in one call** ← chosen
2. Move grounding to `extract_findings` (defeats premise — slop is upstream
   of extraction, lives in user-visible prose)
3. Add a separate `validate_review` pass between (3 model calls/review;
   highest fidelity, highest latency, most code)

**Chosen:** option 1. Rationale:

- Eliminates the second model call (`extractor.py:197` re-prompts in
  JSON mode today — this is the redundancy that lets prose and findings
  drift apart).
- Findings emerge already-structured and already-grounded; no regex
  fallback needed.
- The verbatim-excerpt schema field forces the model to look at the file
  before claiming, killing pattern-matched boilerplate at the source.
- A back-compat shim in `save_review` extracts the prose markdown for
  user-facing review files, preserving the on-disk format.

This is upstream of the v0.2 incident schema. Land grounding first, then
v0.2 Piece 1.

---

## Adversarial cases the design must handle

**G1 — Model emits a finding whose `verbatim_excerpt` doesn't appear in
the cited line range.** This is the slop case. The validator must drop
the finding (not the entire review) and log at WARNING with the file,
line range, and excerpt. Other findings in the same review are
preserved. Budget impact: lossy — the rejected finding is gone from this
review.

**G2 — Model emits findings on a chunk boundary where the excerpt
spans two chunks.** Findings reference `(file, line_start, line_end)`
in the *file's* coordinate space, not the chunk's. The validator opens
the file (already in scope at `processor.py:459` via `file_change.content`)
and slices on file-line numbers. Chunk-boundary edge case is moot.

**G3 — Model omits `verbatim_excerpt` despite schema constraint.**
Ollama's structured-output mode enforces the schema; the response
either matches or the parse fails. If parse fails, fall back to "prose
only, zero findings persisted" — the user still gets a review, the
memory just gets nothing for this file this round. Logged at ERROR.

**G4 — Model copies the excerpt with whitespace drift (tabs vs spaces,
trailing whitespace).** The validator normalizes whitespace on both
sides before substring-matching: collapse runs of whitespace to single
spaces, strip leading/trailing. Exact-byte match is too strict for
LLM output; whitespace-normalized match is the standard pattern in
citation-grounding research.

**G5 — File content changed between read and validate.** The
`file_change.content` captured at watch time is the source of truth;
validation slices that, not a fresh `read_text()`. Avoids race with
in-progress edits.

**G6 — Empty review (model says "no issues found").** Schema permits
`findings: []`. Prose markdown still goes to `save_review`. No findings
persisted. No warnings. This is the happy path.

**G7 — Quote-first prompt ordering interacts with chunked input.**
For files chunked by `chunk_by_lines`, each chunk gets its own review
call. The evidence-first instruction must say "quote from THIS chunk
only." Otherwise the model can fabricate excerpts from imagined wider
context. Schema includes the chunk's line range in the prompt header
so the model anchors to it.

---

## Schema (the JSON shape Ollama returns)

```json
{
  "summary": "string — high-level review prose, markdown",
  "findings": [
    {
      "line_start": 76,
      "line_end": 80,
      "category": "reliability",
      "severity": "high",
      "verbatim_excerpt": "@retry(retry=retry_if_exception_type(...))",
      "description": "predicate retries on ReadTimeout; under stream=False this re-queues a stuck-model hang up to 5x with exponential backoff"
    }
  ]
}
```

Notes:

- `summary` is the prose markdown that currently flows out of
  `generate_review`. Keeps `save_review` working unchanged.
- `findings[]` replaces the second model call's output.
  `verbatim_excerpt` is mandatory.
- `file` is NOT in the per-finding object — it's implicit from the
  caller's context. Avoids the model fabricating cross-file claims.
- Fields `line_start`, `line_end`, `category`, `severity`, `description`
  match the existing `_REQUIRED_KEYS` set at `extractor.py:17`. Adding
  `verbatim_excerpt` is the only new required key.

---

## Implementation — three pieces

### Step 1: schema-constrained `generate_review` + validator (~1 day)

> **SHIPPED** (810fc02 + 720972a). Prose below is the original pre-build plan. See audit: `docs/superpowers/plans/2026-05-15-implementation-audit.md`.

**Files:** `ollama_sentinel/processor.py`, `ollama_sentinel/extractor.py`,
`tests/test_processor.py`, `tests/test_extractor.py`

After Pre-1 lands (widening `response_format` to `Optional[Union[str, dict]]`),
the wiring is:

1. Define `_REVIEW_SCHEMA` constant in `processor.py` (the JSON shape
   above as a Python dict — Ollama accepts this directly).
2. `FileProcessor.generate_review` (`processor.py:459-501`) changes its
   return type from `str` to `dict[str, Any]` containing keys `summary`
   (str) and `findings` (list[dict]). Each call to
   `ollama_client.generate_review` passes `response_format=_REVIEW_SCHEMA`.
3. New helper in `extractor.py`:
   `_validate_verbatim(finding: dict, file_content: str) -> bool` —
   slices `file_content.splitlines()[line_start-1:line_end]`, joins,
   normalizes whitespace, returns `verbatim_excerpt in slice`. ~30 lines.
4. `extract_findings` is renamed `validate_findings` (its job is now
   filtering, not extraction). Removes the second model call at
   `extractor.py:197`. Iterates pre-structured findings,
   `_validate_verbatim` filters, `_parse_finding` validates other
   required keys.
5. Add `verbatim_excerpt` to `_REQUIRED_KEYS` at `extractor.py:17`.
6. **Remove `_extract_from_markdown` regex fallback** (`extractor.py:133-177`)
   — it's the slop generator the May-3 retro complained about.
   Pattern-matches keywords without ever consulting source. Once
   schema-constrained output is in place, the fallback is the only
   ungrounded path left. Keep behind a `--no-grounding` flag if
   debug-only access is wanted; otherwise delete.
7. `save_review` (`processor.py:541-549`) changes input from a `str`
   review to the `dict` — extracts `["summary"]` and writes as before.

**Test guarantees (not mechanisms):**

- A schema-conformant model response with one valid finding round-trips
  to one persisted Finding row with `verbatim_excerpt` populated.
- A schema-conformant response where one finding's `verbatim_excerpt`
  doesn't substring-match the cited range produces zero persisted rows
  for that finding, a WARNING log, and other valid findings in the same
  response still persist.
- A response with `findings: []` persists no findings, writes the
  prose to disk, and emits no warnings.
- A response that fails Ollama's schema validation falls back to prose
  output with `findings: []` and ERROR log.
- The regex fallback test surface is removed (or gated behind the debug
  flag if kept).

### Step 2: quote-first prompt ordering (~2h)

> **SHIPPED** (d3262d6; made grounding-conditional in 47f1929). Prose below is the original pre-build plan. See audit: `docs/superpowers/plans/2026-05-15-implementation-audit.md`.

**Files:** `ollama_sentinel/context/recipes.py`, `tests/test_recipes.py`

`build_review_context` (`recipes.py:48-91`) currently orders sections
`FILE` (MUST_FIT 70%) → `PRIOR UNRESOLVED ISSUES` (OPTIONAL 25%). Add a
new `Section(name="INSTRUCTIONS", priority=Priority.MUST_FIT)` at index 0
containing:

```
For each issue you flag, provide:
1. The exact line range (line_start..line_end).
2. The verbatim excerpt from those lines (no paraphrasing).
3. Your claim about that excerpt.

If you cannot quote verbatim from the file, do not flag the issue.
The excerpt and claim are required; the schema will reject findings
without them.
```

This stacks multiplicatively with Step 1 — it makes evidence-first
tokens reach the schema's `verbatim_excerpt` slot naturally.

**Test guarantees:**

- The instruction block appears before the file block in the rendered
  prompt.
- Removing the instruction block (under a feature flag for ablation)
  measurably increases the WARNING-log rate on the same input set.

### Step 3: regression test against the May-3 slop set (~1h)

> **SHIPPED** (2f1e18e — `tests/test_grounding_regression.py`, R1-R4 slop + P1-P4 real-source positive cases). The May-3 retro never snapshotted raw model responses, so fixtures are synthetic-slop / verbatim-real-source rather than replayed captures — see the P1-P4 header comment and the audit doc. See audit: `docs/superpowers/plans/2026-05-15-implementation-audit.md`.

**Files:** `tests/test_grounding_regression.py` (new)

Capture three real reviews from the May-3 retro that exhibited slop
(magic-numbers boilerplate on files without magic numbers, stale
numeric quotes). Snapshot the file content + the model response.

**Test guarantee:** running these inputs through the post-Step-1
pipeline produces zero findings (or a documented-as-acceptable subset
that is verifiable). This is the empirical seal on the fix.

---

## What this does NOT include

- **Agentic `read_file_lines` tool.** Synthesis Output 3 explicitly
  deprioritized this for 4-7B local models — DeepSeek-Coder-6.7B hits
  88% citation compliance with prompt+schema alone, and small models
  burn tokens wandering. Defer to v0.3 if needed.
- **LLM-as-judge critique loop.** Synthesis: a deterministic substring
  validator is cheaper, faster, and strictly more reliable than a second
  4B pass. Use only for *style* dimensions where exact-match doesn't
  apply (none of which exist in v0.2).
- **Codebase-RAG over prior findings.** Already exists via
  `ViolationDB.get_neighbors_by_similarity`. Downstream of review
  quality, won't fix slop.
- **Re-grounding for triage and research.** Triage runner
  (`triage/runner.py`) and research agent both call models too. Their
  output formats are different (diagnosis prose, not findings) and out
  of scope. If they exhibit similar slop, file separately.
- **Streaming output mode.** Ollama's `format` parameter is incompatible
  with streaming. The current code is non-streaming
  (`processor.py:84`); no change needed. If streaming is added later,
  grounding stays — Ollama supports schema in streaming since v0.6.

---

## Validation — the empirical test

> **SHIPPED & CLOSED.** Item 1 (slop inputs → zero findings): R1-R4. Item 2
> (correct findings round-trip un-rejected): `test_correct_finding_on_real_source_is_not_rejected`
> (P1-P4) using verbatim real source from `processor.py` @ 47f1929 — closes
> the synthetic-positive gap the 2026-05-15 audit flagged. Item 3 (full
> suite green post-removal): 483 passed / 15 skipped.

Before this spec ships to merge:

1. Run the post-Step-1 pipeline against the three May-3 slop inputs.
   The test in Step 3 must pass.
2. Run the post-Step-1 pipeline against three reviews known to be
   correct (pick from the existing review history where the model
   caught a real bug). The findings must round-trip without being
   rejected by the validator. If they're rejected, the validator is
   too strict (whitespace normalization wrong, line-range off-by-one,
   etc.) and needs revision before ship.
3. Confirm via `pytest tests/ -q` that the full suite passes after the
   regex-fallback removal (or that any breaking tests are rewritten,
   not silenced).

---

## Ground truth at the time this spec was written

- master @ `5c1e4a6`, working tree clean.
- Pre-1 (response_format type widening) **not yet shipped** — required
  before Step 1.
- Current `OllamaClient.generate_with_model.response_format`:
  `Optional[str]` (`processor.py:89`).
- Current `_REQUIRED_KEYS`: `{line_start, line_end, category, severity,
  description}` (`extractor.py:17`).
- Current `extract_findings`: re-prompts in JSON mode at
  `extractor.py:197`. To be eliminated.
- Current `_extract_from_markdown` regex fallback: 45 lines at
  `extractor.py:133-177`. To be removed (or feature-flagged).
- Current `generate_review` callers: 4 sites in `processor.py`
  (`477, 482, 490, 501`), 1 in `extractor.py:197` (going away), 1 in
  `watcher.py:218` (consumes the dict return type).
- Existing JSON-mode wiring: `payload["format"] = response_format` at
  `processor.py:131` already passes through; only the type and the
  schema constant are missing.
