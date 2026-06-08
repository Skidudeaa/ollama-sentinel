# Pattern Promotion → Project Guardrails — Requirements

Created: 2026-06-08
Status: ready for planning
Topic: the Finding→Incident→Pattern rung (`docs/VISION.md`)

## Summary

A project **guardrail** layer for Ollama Sentinel: named, curated rules injected
into reviews as explicit checks the model must apply. A guardrail is born one of
two ways — the user **authors one directly** (the primary path, since corroborated
incidents are sparse early), or the system **proposes a candidate** once ≥3
distinct corroborated findings share a semantic shape and the user confirms it.
This is the Finding→Incident→**Pattern** rung from the vision, delivered as a
usable rulebook rather than something gated on incident volume.

---

## Problem Frame

The sentinel's memory currently stops at two rungs: **Findings** (model opinions)
and **Incidents** (objective corroboration). Nothing turns recurring, confirmed
failures into a durable, forward-looking rule that shapes future reviews. The
recurring-violations dashboard panel shows repetition, but repetition of a model
opinion is the model agreeing with itself — it is not a learned guardrail.

The north star (`docs/VISION.md`): *every future diff is reviewed with the
codebase's failure history as context.* Pattern promotion is the mechanism that
closes that loop — but a pure auto-promotion design stalls in practice because
corroborated incidents accrue slowly, so a codebase could run for months before
any pattern reaches the ≥3 threshold. The feature must deliver value before that
volume exists, which makes manual authoring first-class rather than an afterthought.

---

## Actors

- **Developer** — the single user. Authors guardrails, curates candidates,
  reads guardrail-flagged findings. Sole curation authority; nothing enforces
  without their confirmation.
- **Review model** (local Ollama) — evaluates active guardrails against the
  code under review and emits violations as findings.
- **The sentinel** — clusters corroborated findings, proposes candidates,
  injects active guardrails into review context, records provenance.

---

## Requirements

- **R1.** A guardrail is a named, project-specific rule carrying a
  natural-language assertion the review model evaluates against code, plus
  optional scope (e.g., category and/or path) that bounds where it applies.
- **R2.** **Manual authoring (primary).** The developer can create a guardrail
  directly — name, assertion, optional scope — with no incident history required.
- **R3.** **Auto-promotion (compounding).** When ≥3 *distinct* findings sharing a
  semantic shape (same category + embedding cluster) are each corroborated by ≥1
  incident, the sentinel surfaces a **candidate** guardrail for review.
- **R4.** **Curation.** Every guardrail — manual or promoted — is user-controlled:
  confirm, edit (name/assertion/scope), disable, or dismiss. A candidate never
  enforces until confirmed.
- **R5.** **Enforcement via injection.** Active guardrails are injected into the
  review prompt as explicit checks; the model flags violations as findings.
- **R6.** **Relevance-scoped injection.** Guardrails are ranked by relevance to
  the file/diff under review and respect the existing review token budget — the
  system does not inject every guardrail into every review.
- **R7.** **Provenance.** A finding produced because of a guardrail records which
  guardrail produced it.
- **R8.** **Evidence integrity.** A guardrail-caused finding may reinforce its
  originating guardrail (or count toward promotion of that shape) only when
  corroborated by a hard signal (`test_failure` or `fix_commit`). `manual_confirm`
  does not count toward a guardrail's own strengthening.
- **R9.** **Surfacing.** Active guardrails and pending candidates are inspectable
  via CLI verbs in the existing finding-management family (list / confirm /
  edit / dismiss) and visible in the dashboard.
- **R10.** **Lifecycle.** A guardrail can be disabled or dismissed at any time;
  disabled guardrails are not injected.

---

## Key Decisions

- **A guardrail is both a curated rulebook entry and a proactive matcher.** The
  human-in-the-loop curation step is what makes proactive matching trustworthy —
  and it satisfies the VISION non-goal "auto-intervention would be nagware":
  nothing enforces until the developer confirms.
- **Matching is LLM-evaluated, not a deterministic linter.** Shape = a semantic
  cluster (by finding embedding + category); enforcement = the model checks the
  assertion against code during review. Chosen for flexibility and conceptual
  recurrence capture; accepts probabilistic matching over linter precision.
- **Promotion requires ≥3 *distinct corroborated* findings.** Not raw incident
  count (one stubborn bug re-confirmed 3× is one location, not a cross-codebase
  rule) and not uncorroborated findings (that re-imports the "echo" problem).
  This keeps the objective-evidence thesis intact.
- **Manual authoring is the primary path; auto-promotion is the compounding
  layer.** Driven by the observation that incidents are sparse early — the
  feature must deliver value on day one, before any pattern reaches ≥3.
- **Self-caused findings reinforce only via hard corroboration.** A guardrail
  that flags new code can have those findings count toward its own strength only
  when an objective signal (test failure / fix) confirms them — a real test
  failure is evidence regardless of what prompted the look; soft self-confirmation
  is excluded. Requires per-finding provenance (R7) to enforce.

---

## Scope Boundaries

### In scope
- Manual guardrail authoring, curation, and lifecycle (R1, R2, R4, R10).
- Relevance-scoped injection of active guardrails into reviews (R5, R6).
- Finding provenance and the hard-corroboration integrity gate (R7, R8).
- Auto-promotion **candidate** surfacing from corroborated shapes (R3, R9).

### Deferred for later
- A deterministic/AST matcher engine — reconsider if LLM matching proves noisy.
- Automatic staleness pruning of guardrails (analogous to `prune` for findings).
- Cross-repo or shared/exported guardrail libraries.

### Non-goals (outside this feature's identity)
- Auto-enforcement without curation (nagware).
- Promotion from uncorroborated findings or from raw incident counts.

---

## Phasing

- **Phase 1 — Guardrails that work without volume.** Manual authoring,
  relevance-scoped injection, curation/lifecycle, provenance. Ships the full
  value loop with zero incident history (R1, R2, R4–R7, R9, R10).
- **Phase 2 — Compounding via auto-promotion.** Semantic clustering of corroborated
  findings, candidate surfacing, the integrity gate (R3, R8). Layered on Phase 1's
  guardrail artifact and injection path.

---

## Success Criteria

- A developer can author a guardrail and see it applied in the very next review,
  with no prior incidents.
- Guardrail-flagged violations appear as findings carrying guardrail provenance.
- Adding more guardrails does not blow the review token budget — only the most
  relevant are injected.
- Once ≥3 distinct corroborated findings share a shape, a candidate guardrail is
  proposed for confirmation (and a soft self-confirmed finding never triggers
  promotion on its own).

---

## Open Questions

- **Candidate assertion authoring.** Does an auto-promoted candidate arrive with
  a system-suggested assertion (LLM-summarized from the cluster) that the user
  edits, or does the user write the assertion from scratch at confirmation?
- **Clustering cadence.** When does auto-promotion clustering run — on incident
  creation, on a watcher tick, or only when the user invokes a `patterns`/
  `guardrails` command? (Affects cost and surprise.)
- **Relevance signal for injection.** What decides guardrail↔diff relevance —
  embedding similarity of the assertion, declared category/path scope, or both?
- **Guardrail staleness.** Should guardrails that stop matching (or whose anchoring
  shape disappears) be auto-disabled or surfaced for pruning, mirroring `prune`?

---

## Dependencies / Assumptions

- **Reuses existing semantic infra** — the `qwen3-embedding:4b` hot-path embedder
  (`OllamaEmbedder`) and `SemanticRetriever` for both shape clustering and
  injection relevance ranking.
- **Reuses the token-budgeted context assembler** — `build_review_context` already
  injects a retriever-ranked, budget-capped `PRIOR UNRESOLVED ISSUES` section;
  guardrail injection follows the same mechanism as a distinct, higher-priority
  section.
- **Reuses `ViolationDB`** — findings/incidents and the `confirming_signal` field
  (`test_failure` / `fix_commit` / `manual_confirm`) back the corroboration gate.
- **Assumes incidents remain sparse early**, which is the load-bearing reason
  manual authoring is first-class. If real-world incident volume turns out high,
  the phasing emphasis could shift toward auto-promotion.
