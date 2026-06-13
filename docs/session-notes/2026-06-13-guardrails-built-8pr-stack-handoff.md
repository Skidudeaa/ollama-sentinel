# Session handoff — 2026-06-13: Pattern promotion → project guardrails BUILT

## What happened

Executed the **Pattern promotion → project guardrails** plan
(`docs/plans/2026-06-08-001-feat-pattern-promotion-guardrails-plan.md`)
end-to-end via `/ce-work` — all 8 units, TDD per unit, one stacked PR each.
This is the v0.3 north-star feature: the **Finding → Incident → Pattern** rung
from `docs/VISION.md`.

Also shipped the independent **truncated-grounded-review salvage** fix as
**PR #36** (was committed-but-unshipped at session start).

## The stack (pending merge, bottom → top, base master)

| PR | Unit | Summary | Files |
|----|------|---------|-------|
| #37 | U1 storage + provenance | `guardrails` table + CRUD + `Guardrail`; finding `guardrail_id` via additive `_migrate` | `violation_db.py` |
| #38 | U2 CLI authoring/lifecycle | `guardrail add/list/edit/disable/enable/dismiss` sub-app | `cli.py` |
| #39 | U3 relevance-scoped injection | `PROJECT GUARDRAILS` section above PRIOR UNRESOLVED; scope filter + retriever rank + soft_budget | `context/recipes.py`, `processor.py` |
| #40 | U4 provenance capture | best-effort `attribute_guardrail_provenance`; watcher wiring | `processor.py`, `watcher.py` |
| #41 | U5 dashboard panel | read-only active-guardrails panel | `dashboard.py` |
| #42 | U6 shape clustering | `get_corroborated_findings` selector + new `guardrails.py` (`detect_candidates`) | `violation_db.py`, `guardrails.py` |
| #43 | U7 candidate surfacing/curation | `guardrail candidates/promote/reject`; LLM `draft_assertion` + fallback; signature suppression | `cli.py`, `guardrails.py` |
| #44 | U8 evidence-integrity gate | `counts_toward_strength` (self-caused → hard signal only); pre-filters `detect_candidates` | `guardrails.py` |

Suite: **699 → 803 passed / 16 skipped**, green at every commit. The U8 tip
(all 8 units combined) is green. This docs PR (`docs/guardrails-shipped`) rides
the tip.

## Merge order

Bottom-up: **#37 → #38 → #39 → #40 → #41 → #42 → #43 → #44 → (this docs PR)**.
GitHub auto-retargets each child to master as its parent merges. **#36** (salvage)
is independent — merge anytime.

## Two transparent deferrals (flagged in PR bodies)

1. **Live dashboard *pending*-candidate view** — intentionally NOT built in U7.
   Running `detect_candidates` on the dashboard's 1s poll loop would violate
   **KTD4** (clustering is on-demand only). Confirmed candidates already show in
   the U5 panel as `source=promoted` ("auto"). A cached / slow-cadence panel
   would re-enable a live view — clean follow-up.
2. **U8 gate scope** — implemented as a *global* exclusion of self-caused-soft
   findings, not *scoped-to-originating-guardrail*. This is the stricter, safe
   direction (cannot create false candidates) and satisfies every plan scenario.
   Revisit only if it proves too aggressive in practice.

## Resume next time

1. Merge the stack (#37→#44, then #36).
2. Optionally pick up the two deferrals above.
3. Then the **v0.3 shared substrate** moat play (lift `ImportResolver`, unify
   `Finding`/`ImpactItem`, bidirectional impact↔incident) — see `docs/VISION.md`.

## Minor cleanup noted

`tests/test_cli.py` carries one cosmetic unused `Guardrail` import (introduced in
U2, Pyright-only lint, no test impact) — fold into whatever next touches that file.
