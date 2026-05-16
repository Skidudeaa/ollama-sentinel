# Handoff — Plan Audit + v0.2 Incident Schema Pieces 1–3

**Date:** 2026-05-16
**Work:** Audited all 6 plans against landed code, closed 2 deferred
reviewer-grounding flags, then implemented v0.2 incident-schema Pieces
1–3 TDD as a stacked PR chain. Five PRs open (#7 docs; #8→#9→#10 the
v0.2 stack; this handoff is a 6th, off master).

This is the resume point. CLAUDE.md was deliberately **not** updated this
session (PR #7 already edits it; a second editor would create a cross-PR
conflict) — the canonical v0.2 progress record is the Status line in
`docs/superpowers/plans/2026-05-02-v02-incident-schema.md` on the stack.

---

## What shipped

### PR #7 — `docs/2026-05-15-implementation-audit` → master (docs only)

Implementation-vs-plan audit of all 6 plans (6 parallel read-only
agents). New consolidated report
`docs/superpowers/plans/2026-05-15-implementation-audit.md`. Verdicts: 4
SHIPPED, context-builder + triage SHIPPED, v0.2 NOT STARTED (then).

**Headline:** CB-1 (dedupe impact-report formatters) was mis-tracked as
OPEN in `followups.md` AND CLAUDE.md "Pickable next moves" #1 — actually
closed by commit `1313681`. Corrected both, plus stale test baseline
`378`→`~494` and plan status headers.

Also closed the 2 reviewer-grounding flags the audit deferred:
- **Validation item 2** was synthetic-only. Added
  `test_correct_finding_on_real_source_is_not_rejected` (P1–P4) in
  `tests/test_grounding_regression.py` using verbatim real source frozen
  from `processor.py` @ 47f1929, each with a uniqueness guard. Path A
  (replay pre-grounding `.ollama_reviews/` captures) is structurally
  impossible — they predate the `verbatim_excerpt` field.
- **Plan-body staleness** → per-section `> **SHIPPED**` markers, not a
  rewrite (frozen "Ground truth" section stays frozen by design).

### PR #8 — `feat/v02-piece-1` → master

`Incident` dataclass + `incidents` table (FK→`findings.id`) + `PRAGMA
foreign_keys=ON` + `_migrate` extension (`triggering_commit_sha`,
`fix_commit_sha`, idempotent, A7) + 5 CRUD methods + behavioral
`mark_resolved(*, fix_commit=)`. 9 tests (`TestIncidents`). Plan's
mandatory schema acceptance test executed against a real bug
(`1b3c127` dashboard churn) — schema captured the full causal chain,
no forced fields.

### PR #9 — `feat/v02-piece-2` → `feat/v02-piece-1` (stacked)

`ollama_sentinel/hooks.py`: `install_hooks` (writes executable
`.git/hooks/post-commit`, never clobbers, raises on non-repo),
`record_commit` (GitPython HEAD/SHA → touched files →
`link_commit_to_findings`). CLI verbs `install-hooks` / `record-commit`
+ shared `_load_config_or_exit` helper. 9 tests (`tests/test_hooks.py`,
real git repos, no mocks).

### PR #10 — `feat/v02-piece-3` → `feat/v02-piece-2` (stacked)

`confirm <finding_id> [--note]` verb: inserts a `manual_confirm`
Incident, Finding stays open. Nonexistent ids rejected via Piece 1's FK
(`sqlite3.IntegrityError` → exit 1). 3 tests
(`tests/test_cli.py::TestConfirmCommand`).

**Suite at the top of the stack: 500 passed / 15 skipped.**

---

## Merge order

```
#7  docs/2026-05-15-implementation-audit  → master   (independent)
#8  feat/v02-piece-1                       → master
#9  feat/v02-piece-2                       → #8       (auto-retargets to master when #8 merges)
#10 feat/v02-piece-3                       → #9       (auto-retargets when #9 merges)
```

Merge #8 → #9 → #10 in order. #7 is independent and can merge anytime.

**Known trivial conflicts on merge** (resolve toward the *later* truth):
- `docs/superpowers/plans/2026-05-02-v02-incident-schema.md` **Status:**
  line — PR #7 sets it to "NOT STARTED (parked)"; the stack sets it to
  "Pieces 1,2,3 SHIPPED". The stack's is correct once it lands.
- `CLAUDE.md` — PR #7 edits the "Pickable next moves" / breadcrumbs.
  Nothing else touched it this session. After #7 + the stack land, add a
  CLAUDE.md "Recent landings" entry for v0.2 P1–3 (deferred to avoid the
  cross-PR conflict).

---

## Resume here — v0.2 Pieces 4 & 5

Plan: `docs/superpowers/plans/2026-05-02-v02-incident-schema.md`.

### Piece 4 — pytest plugin (~half day, the substantive one)

`ollama_sentinel/pytest_plugin.py` (new) + `pytest11` entry point in
`pyproject.toml` + `tests/test_pytest_plugin.py`. On
`pytest_sessionfinish`, map failed-test locations to open Findings
within a ±5-line tolerance (A6), create `test_failure` Incidents,
populate `suspect_commits`.

**GOTCHA — the spec's frozen "Ground truth" is stale here.** It says
`ImportResolver` lives in `research_agent/tools/` imported via
try/except. It was **promoted to `ollama_sentinel.context`** (commit
`0176b2f`, part of the grounding work). Use the new location. This is
the one stale fact that actually bites Piece 4 (it did not affect
Pieces 1–3). Verify with `grep -rn "class ImportResolver"
ollama_sentinel/ research_agent/` before wiring.

**Critical constraint from the plan:** zero-cost when disabled — no
config file or no DB ⇒ plugin does nothing, no import-time side effects.

### Piece 5 — `incidents` CLI + docs (~2h, bookend)

`incidents` subcommand (table/json, `--days`, `--finding`). Update
VISION.md / CLAUDE.md / README. Depends on Piece 1 only.

### Branching convention used this session

- Feature work never rides a docs PR (we split Piece 1 off the audit
  branch mid-session to keep #7 docs-only — see the AskUserQuestion
  decision in the transcript).
- Stack linearly when a piece reuses an earlier piece's helper
  (Piece 3 reuses Piece 2's `_load_config_or_exit`), even though the
  plan calls 2/3/4 logically parallel — linear avoids
  duplicate-introduction conflicts.
- Per-piece discipline: TDD (watch-fail-first), and where the plan
  lists a method/wrapper without a guarantee test, add one (Piece 1's
  9th test, Piece 2's CLI-wrapper tests) — documented as a transparent
  deviation in the commit + plan marker.
- Each piece updates the plan's Status line + adds a
  `> **SHIPPED**` marker under its heading.

---

## Persistent gotchas (not session-specific)

- **pyright noise is not real defects.** `import git` / `import pytest`
  / `from .hooks` "could not be resolved" = pyright not pointed at the
  venv (deps ARE installed). The `_make_finding`/`_make_incident(**overrides)`
  `str | int` argument warnings are a pre-existing test-helper pattern
  (identical on the original `_make_finding`). Don't "fix" these.
- **Config fixtures need full `OllamaModelConfig`.** A minimal test YAML
  must have `ollama.models.default.{name, system_prompt}` — both
  required. Reuse `tests/test_cli.py::_make_report_config`.
- **`mark_resolved` has zero non-test callers** (verified incl.
  `_archive`), so its behavioral change has no breakage surface — until
  Piece 4/5 wire it.
- Full suite ≈ **500 / 15** at the top of the v0.2 stack; ~488 on Piece-1
  alone off master; ~479 pure master; ~483 with PR #7's +4 grounding
  tests. The number depends on which branches are in play — don't treat
  a single figure as canonical across branches.
