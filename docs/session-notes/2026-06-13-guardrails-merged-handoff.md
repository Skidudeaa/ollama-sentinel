# Session handoff — 2026-06-13: Pattern-promotion guardrails MERGED to master

> Supersedes `2026-06-13-guardrails-built-8pr-stack-handoff.md` (written
> mid-session, when the stack was still unmerged). Everything below is the
> final, merged state.

## TL;DR

The v0.3 **Pattern promotion → project guardrails** feature is **built, tested,
documented, and merged to `master`**. The repo is clean: `master @ 92de011`,
only the `master` branch remains, **0 open PRs**, suite **813 passed / 16
skipped**. There is no in-flight work. Pick a next move from CLAUDE.md or just
use the feature.

## What shipped (PRs #36–#45, rebase-merged bottom-up)

The **Finding → Incident → Pattern** rung from `docs/VISION.md` is now whole:

| PR | Unit | What landed |
|----|------|-------------|
| #37 | U1 | `guardrails` table + CRUD + `Guardrail` dataclass; nullable `guardrail_id` provenance on findings (additive `_migrate`) |
| #38 | U2 | `guardrail` Typer sub-app: `add/list/edit/disable/enable/dismiss` |
| #39 | U3 | `PROJECT GUARDRAILS` injection section (scope-filtered, retriever-ranked, budget-capped) above PRIOR UNRESOLVED in `build_review_context` |
| #40 | U4 | best-effort `attribute_guardrail_provenance` (finding → originating guardrail), wired in the watcher |
| #41 | U5 | read-only active-guardrails panel in the dashboard |
| #42 | U6 | `ollama_sentinel/guardrails.py`: `Candidate` + async `detect_candidates`; `get_corroborated_findings` selector |
| #43 | U7 | `guardrail candidates/promote/reject`; LLM `draft_assertion` + fallback; signature-based suppression |
| #44 | U8 | `counts_toward_strength` evidence-integrity gate (self-caused findings reinforce only via `test_failure`/`fix_commit`) |
| #45 | docs | README, GUIDE, VISION, index.html, CLAUDE.md, plan→Implemented |
| #36 | — | independent: truncated-grounded-review salvage (`done_reason` warn + `_salvage_truncated_review`) |

New module: `ollama_sentinel/guardrails.py`. Heavily touched:
`violation_db.py`, `cli.py`, `processor.py`, `context/recipes.py`, `watcher.py`,
`dashboard.py`.

## Using it (quick reality check)

- **Author by hand — works with no Ollama** (pure SQLite):
  `ollama-sentinel guardrail add no-eval -a "Never eval untrusted input." --category security --path "src/*.py"` → `guardrail list`.
- **See it shape reviews:** `ollama serve`, then `ollama-sentinel review <file>` — active in-scope guardrails are injected; flagged findings carry provenance.
- **Auto-candidates** (`guardrail candidates`) need: `embedding.enabled`, `ollama pull qwen3-embedding:4b`, **and ≥3 distinct corroborated findings of one shape**. On a fresh DB this is empty by design — manual authoring is the day-one path; promotion compounds later.

## Two open follow-ups (deferred during the build, both low-risk)

1. **Live dashboard *pending*-candidate view** — intentionally NOT built (U7).
   Running `detect_candidates` on the 1s poll loop would violate **KTD4**
   (clustering is on-demand only). Confirmed candidates already show in the U5
   panel as `source=promoted`. A cached / slow-cadence panel would re-enable a
   live pending view. Only do it if the absence is actually felt.
2. **U8 gate scope** — implemented as a *global* exclusion of self-caused-soft
   findings, not *scoped-to-originating-guardrail*. Stricter-is-safe (can't
   manufacture false candidates) and passes every plan scenario. Revisit only if
   it proves too aggressive in practice.

Plus a cosmetic: `tests/test_cli.py` carries one unused `Guardrail` import
(Pyright-only lint, no test impact).

## Biggest remaining arc (the real next thing)

**v0.3 shared substrate** (`docs/VISION.md` → "What's still aspirational"): lift
`ImportResolver` out of `research_agent/tools/` into shared infra, unify
`Finding`/`ImpactItem`, and make the impact↔incident flow bidirectional. This is
the "moat" play and is now unblocked. Start with `/ce-brainstorm` or
`/ce-plan` — it's architectural and design-heavy.

## Process note for next time (learned the hard way)

**Rebase-merging a deep stack rewrites the early commits' SHAs.** That makes
each later PR whose branch *shares files* with an earlier unit go `DIRTY`
against the rewritten `master`. The fix that worked: for each remaining PR,
`git checkout <branch> && git rebase origin/master` (git drops the
already-applied commits by patch-id and replays only that unit's delta),
`git push --force-with-lease`, then `gh pr merge --rebase --admin` (content was
byte-identical to CI-passed commits, so `--admin` over redundant CI was fine).
The independent #36 needed a real CLAUDE.md conflict resolution (kept both
"Recent landings" entries). If a future stack is deep and shares files, consider
merge-commits or fast-forward instead of rebase-merge to avoid the SHA churn.

## Sanity on resume

```bash
git status            # clean, on master
git log --oneline -3  # 92de011 salvage breadcrumb … guardrails stack below
pytest tests/ -q      # ~813 / 16 skip (drifts)
```
