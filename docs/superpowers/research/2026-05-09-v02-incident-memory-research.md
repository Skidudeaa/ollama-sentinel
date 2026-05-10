# Research synthesis — v0.2 incident memory

**Date:** 2026-05-09
**Inputs:** three parallel R&D agents dispatched after the schema walk-through
exposed gaps in `docs/superpowers/plans/2026-05-02-v02-incident-schema.md`.
Outputs cached as task transcripts under `/private/tmp/claude-501/...`; the
synthesis below is the durable record.

**Verdict:** v0.2 spec needs three concrete deltas before Piece 1 ships, and
one upstream prerequisite (reviewer grounding) should land first or in
parallel — without it, the memory is grading homework against a model that
ignores its own homework.

---

## Output 1 — bug-attribution / SZZ research (Piece 4 input)

**Question asked:** how to map a test failure (file:line) backward to the
commit that likely introduced it, for the pytest plugin's `suspect_commits`
ranking. The spec's current sketch — "last 5 commits that touched the failing
file or its direct imports" — is thin.

**Key findings:**

1. B-SZZ via PyDriller is the practical baseline (recall ~72%, precision
   ~9-10%). AG-SZZ's comment-line filtering actively hurts recall —
   recommendation against using it.
2. ~25% of real bug-inducing commits are *ghost commits* (no deleted lines
   in the fix) or *cross-file* (bug in a different file than where the fix
   landed). Standard SZZ misses these entirely.
3. **SemBIC (FSE 2025)** is the strongest coverage-free prior art:
   MRR 0.520, top-1 88/199, +29% over prior SOTA. Replication package on
   Zenodo. Implementation surface is large (AST data-flow tracking across
   historical commits) — not v0.2-sized.
4. **LLM-assisted reranking** (LLM4SZZ, AgentSZZ): F1 0.748 vs B-SZZ 0.507.
   Llama3-8b documented to work. Adds latency to every test failure hook.

**Concrete recommendation for Piece 4 (~half-day budget):**

Combine two static techniques:

- **Import-graph hop-distance × log-recency scoring.**
  `score = 1 / (1 + hop) × 1 / (1 + age_days / 30)` for each commit that
  touched any file in the failing test's AST import closure. Reuses the
  existing `research_agent/tools/import_resolver.py`. ~2-3h.
- **Pickaxe overlay (`git log -S <symbol>`).** Extract the function/class
  name from the failing line via `ast.parse`; run `git log -S` to find every
  commit that added or removed that string. Intersect with the import-graph
  candidate set for ranking. ~1-2h. **This is the high-value addition** —
  it directly addresses the 25% cross-file/ghost-commit gap that pure blame
  misses.

Total: ~3-4h, no new dependencies, beats baseline by addressing transitive
imports + cross-file attribution.

**Park for v0.2.1:** LLM-assisted reranking. `OllamaClient` is already in
the tree; wire top-K candidates through a structured reranking prompt with
the failing test, commit diffs, and commit messages. Behind a feature flag
because of latency.

**Park for v0.3:** SemBIC semantic diffing. MRR gains real, replication
package exists, implementation surface is a half-sprint.

**Citations:**
- LLM4SZZ — https://arxiv.org/abs/2504.01404
- AgentSZZ — https://arxiv.org/abs/2604.02665
- SemBIC (FSE 2025) — https://dl.acm.org/doi/10.1145/3715781
- Fonte (ICSE 2023) — https://arxiv.org/abs/2212.06376
- Neural SZZ (ASE 2023) — https://baolingfeng.github.io/papers/ASE2023.pdf

---

## Output 2 — incident-memory schema research (Piece 1 input)

**Question asked:** validate the Finding/Incident split against production
incident-management and code-quality tools.

**Industry convergence:** the two-layer split (static/latent finding vs.
runtime/corroborated event) is the right shape. SARIF, Sentry, and Datadog
independently converged on it. But three of our schema choices are
sub-industry-standard.

### Delta 1 — split `confirming_signal` into `detection_method` + `confirmation_method`

Datadog separates "how was it spotted" (`monitor | alert | customer_report
| internal | manual`) from "how was it verified" (the corroboration step).
Our single enum `test_failure | manual_confirm | fix_commit` collapses
these. The May-9 retry-storm bug walkthrough exposed exactly this collapse:
*detection* was a runtime monitor (logs), *confirmation* was a manual
rollback / fix commit — different timestamps, different actors.

Replace:

```python
confirming_signal: str   # OLD — single field
```

with:

```python
detection_method: str    # runtime_observation | test_failure | static_analysis | customer_report | internal | manual
confirmation_method: str # test_failure | manual_confirm | fix_commit | regression_test
```

This also resolves the `runtime_observation` gap surfaced in the
walkthrough.

### Delta 2 — replace `blast_radius: list[str]` with structured `impact_scope`

The walkthrough flagged the file-list shape as awkward for runtime/
reliability bugs. Sentry and Datadog both model impact as a typed object,
not a file list.

Replace:

```python
blast_radius: list[str] | None  # OLD — files only
```

with:

```python
@dataclass
class ImpactScope:
    files: list[str] = field(default_factory=list)        # static-bug case
    services: list[str] = field(default_factory=list)     # cross-service runtime case
    severity_if_triggered: str | None = None              # SEV1..SEV5 / null
    customer_facing: bool = False
    impact_start: str | None = None                       # ISO timestamp, runtime case
    impact_end: str | None = None
```

JSON-serialized in SQLite. Static bugs populate `files`; runtime bugs
populate the timing + customer_facing fields. No schema fork.

### Delta 3 — replace `Finding.resolved: bit` with SARIF-shaped `kind` enum

SARIF 2.1.0 distinguishes `open / review / pass / fail`. Our binary
`resolved` collapses three meaningful states into "still flagged."

Replace:

```python
resolved: int  # OLD — 0 or 1
```

with:

```python
kind: str  # opinion | suppressed | confirmed | fixed
```

Mapping: `opinion` (LLM emitted, no Incident yet), `suppressed` (user
dismissed), `confirmed` (≥1 Incident exists), `fixed` (Incident with
`confirming_signal=fix_commit` exists). Replaces the boolean with a state
machine and makes "how many LLM opinions become real incidents" a trivial
query.

Also add to Finding:

```python
first_detected_revision: str | None  # commit SHA at first_seen
```

Symmetric with `triggering_commit_sha` on the Incident side. Lets us
answer "how long did this opinion sit before being corroborated?"

### Migration cost

All three deltas are additive on the SQL side except `Finding.kind`, which
replaces `Finding.resolved`. Migration: `ALTER TABLE findings ADD COLUMN
kind TEXT DEFAULT 'opinion'`; backfill `kind = 'fixed'` where `resolved =
1`, otherwise `'opinion'`; drop the `resolved` column. Idempotent. Follows
existing `_migrate` pattern.

### What the spec got right

- Two-layer split itself.
- `triggering_commit` + `suspect_commits` (matches SARIF `relatedLocations`
  + CodeQL path-problem source/sink).
- A1–A7 adversarial enumeration (this is unusually rigorous for a v0.2
  schema spec; nothing in the comparison set surfaces an A8).

**Citations:**
- SARIF 2.1.0 — https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
- Sentry Issue Platform — https://develop.sentry.dev/backend/issue-platform/
- Datadog Incident Management — https://docs.datadoghq.com/service_management/incident_management/describe/
- CodeQL path-problem queries — https://codeql.github.com/docs/writing-codeql-queries/creating-path-queries/
- Semgrep webhooks schema — https://semgrep.dev/docs/semgrep-appsec-platform/webhooks

---

## Output 3 — grounded LLM code review (upstream prerequisite)

**Question asked:** how to make the reviewer cite verbatim and stop
emitting pattern-matched boilerplate. This is upstream of v0.2 — without
it, incidents corroborate noise.

**Headline:** the May-3 retro plan ("sharper system_prompt + post-hoc
validator") is correct, with one critical upgrade — **the validator must
be mechanical (byte-exact substring match), not an LLM-as-judge**.
Replacing the deterministic check with a second model call is a 2024
pattern that the Dec-2025 citation-grounding work has superseded.

**Two-step ship:**

### Step 1 — schema-constrained Ollama output + deterministic validator (~1 day)

Force findings into a JSON schema via Ollama's `format: <schema>`
parameter:

```json
{
  "file": "ollama_sentinel/processor.py",
  "line_start": 76,
  "line_end": 80,
  "verbatim_excerpt": "@retry(retry=retry_if_exception_type(...))",
  "claim": "predicate retries on ReadTimeout, which under stream=False ...",
  "severity": "high"
}
```

Validator (~30 lines of Python): open `file`, slice `lines[line_start-1:
line_end]`, reject any finding where `verbatim_excerpt not in slice`.

**Effect:** pattern-matched boilerplate cannot survive (no real excerpt to
cite); stale numeric values fail the substring check. Dec-2025
citation-grounding paper reports 100% prevention of fabricated citations
using this interval-arithmetic approach.

Qwen3:4b is mediocre at instruction-following but strong at JSON-mode, so
this plays to the model's strengths.

### Step 2 — quote-first prompt ordering (~2h)

Require `<evidence>verbatim lines</evidence>` *before* `<claim>...</claim>`,
with explicit "if no verbatim evidence exists, omit the finding"
instruction. Stacks multiplicatively with Step 1 because evidence-first
tokens get captured structurally.

### Explicitly deprioritized

- **Agentic `read_file_lines` tool.** Greptile-style. Lift at 4-7B is
  *smaller than expected* — DeepSeek-Coder-6.7B hits 88% citation
  compliance with prompt+schema alone. Small models burn tokens wandering.
  Defer.
- **LLM-judge critique loops.** A deterministic validator is cheaper,
  faster, and strictly more reliable than a second 4B pass. Use only for
  *style* dimensions where exact-match doesn't apply.
- **Codebase-RAG over prior findings.** Already exists via ViolationDB
  semantic recall. Downstream of review quality. Won't fix slop.

**Citations:**
- Citation-Grounded Code Comprehension (Dec 2025) — https://arxiv.org/abs/2512.12117
- De-Hallucinator: Iterative Grounding — https://arxiv.org/html/2401.01701v3
- JSONSchemaBench (ICLR 2025) — https://openreview.net/forum?id=FKOaJqKoio
- Greptile vs CodeRabbit comparison — https://www.greptile.com/greptile-vs-coderabbit
- Constraining LLMs with Structured Output (Glukhov, 2025) — https://www.glukhov.org/post/2025/09/llm-structured-output-with-ollama-in-python-and-go/

---

## Recommended sequencing

```
[Reviewer grounding spec — NEW]
   ↓ Step 1 (schema-constrained output + validator)  ~1 day
   ↓ Step 2 (quote-first prompt)                      ~2h
   ↓
[v0.2 Piece 1 (revised schema with the 3 deltas)]    ~half day + ~2h migration
   ↓
[v0.2 Pieces 2/3/4/5 in parallel as planned]
   └─ Piece 4 uses the import-graph + pickaxe hybrid for suspect_commits
```

The reviewer-grounding work is independently shippable and immediately
useful (cleaner reviews today), so it can land before, alongside, or
slightly after Piece 1 without blocking. Recommendation: ship grounding
first because it changes what data flows into the memory.

## What this synthesis does NOT do

- **It does not revise the v0.2 spec in place.** The spec at
  `docs/superpowers/plans/2026-05-02-v02-incident-schema.md` is the source
  of truth and should be updated by editing it directly with the three
  deltas above. That edit is a separate work item.
- **It does not write the reviewer-grounding spec.** That's a new spec
  under `docs/superpowers/plans/` that should be authored before
  implementation starts. Outline above is sufficient input.
- **It does not run the spec's acceptance test on a second bug.** The
  May-9 retry-storm walkthrough was the first acceptance bug; a second
  bug that the model *did* flag (something like a missing null check)
  should be walked through after the deltas are applied to confirm the
  revised schema fits both shapes.

## Ground truth at the time this synthesis was written

- master @ `5c1e4a6`, working tree clean.
- v0.2 spec exists, status "ready for review, then implementation," zero
  code landed.
- Reviewer-slop problem documented in
  `docs/retros/2026-05-03-config-and-timeout-debugging.md` lines 101–109.
- Three research agent transcripts live under `/private/tmp/claude-501/...`
  but are ephemeral; this document is the durable record.
