# v0.2 incident memory — deficit remediation

**Date:** 2026-05-09
**Inputs:** three deficit-matching agents (feasibility, pattern-mapping,
coherence) run against
[`2026-05-09-v02-incident-memory-research.md`](2026-05-09-v02-incident-memory-research.md)
and
[`../plans/2026-05-02-v02-incident-schema.md`](../plans/2026-05-02-v02-incident-schema.md).
**Outcome:** the three deltas in the synthesis survive scrutiny, but
implementation budgets and one architectural decision were wrong. This
doc captures what to fix before Piece 1 starts.

## Top-line corrections to the synthesis

| Synthesis claim | Reality | Source |
|---|---|---|
| Piece 1 is "~half day + ~2h migration" | **Full day minimum** with the `kind` rename rippling into 22 SQL strings, 4 method names, ~30 tests, dashboard copy | feasibility |
| `resolved` → `kind` migration is `ALTER TABLE … DROP COLUMN` | **Breaks on Python 3.10's bundled libsqlite 3.34.** Must use the SQLite 12-step rebuild pattern. ~1-2h + WAL-mode test | feasibility |
| Reviewer grounding wires into existing `format` payload | **`OllamaClient.generate_with_model.response_format: Optional[str]` rejects a dict.** Type widening required across `extractor.py:200` and `tests/test_processor.py:705` | feasibility |
| Reviewer grounding fixes `generate_review` slop | **Architectural decision needed — `generate_review` returns prose, structured findings come from the separate `extract_findings` JSON-mode call. The synthesis collapsed two distinct pipelines.** | feasibility |
| Pickaxe is "extract symbol via ast.parse" | **No helper exists to walk `ast.FunctionDef`/`ast.ClassDef` and resolve enclosing-symbol-by-line.** ~1-2h to write | feasibility |
| `git log -S` is ~1-2h to wire | Wiring is trivial; **the latency budget is the problem** — 50 failures × cold-disk git history scan can violate the spec's "zero-cost when disabled" constraint. Need `--since=HEAD~50` bound + per-symbol timeout | feasibility |
| `ImportResolver` reuse is free | **Lives under `research_agent/tools/`** — pytest plugin in `ollama_sentinel/` requires `[research]` extras. Default install silently degrades | feasibility |

The synthesis's *direction* is correct on every front. The estimates and
one architectural premise are wrong.

## Architectural decision (resolved 2026-05-09)

The May-3 retro complained about slop in `generate_review`'s **prose
output** — markdown the user reads. Structured findings come from a
*separate downstream* `extract_findings` call (`extractor.py:180-232`)
that already uses `response_format="json"`.

Three options were considered:

1. **Change `generate_review` to emit JSON+prose in one call.** ← chosen
2. Move grounding to the `extract_findings` step (defeats premise — slop
   is upstream of extraction).
3. Add a separate `validate_review` pass between (3 model calls/review).

**Chosen: option 1.** Grounding lives in `generate_review`; output schema
contains both `summary: str` (prose markdown for user-facing review file)
and `findings: list[...]` (structured + grounded). A back-compat shim in
`save_review` extracts the prose so the on-disk format is unchanged.
Eliminates the second model call (`extractor.py:197` re-prompt goes
away). See [`../plans/2026-05-09-reviewer-grounding.md`](../plans/2026-05-09-reviewer-grounding.md)
for the full spec.

## Spec edits (apply before Piece 1 starts)

Eight items, in order of severity. Each is a discrete edit to
`docs/superpowers/plans/2026-05-02-v02-incident-schema.md`. None requires
codebase changes.

### CONTRADICTION-1 — Piece 4 still describes the old heuristic
**Spec lines ~373-374.** Currently sketches "last N commits that touched
files in the Finding's 1-hop import neighborhood." Replace with the
import-graph hop-distance × log-recency + pickaxe hybrid from the
synthesis. Update effort to ~3-4h. **Add the latency-bound clause:**
"Pickaxe calls bounded by `--since=HEAD~50` and a 2s per-symbol timeout
to preserve the zero-cost-when-disabled constraint."

### CONTRADICTION-2 — Incident DDL still has `blast_radius TEXT`
**Spec lines ~129-145.** Replace `blast_radius TEXT` column with
`impact_scope TEXT, -- JSON serialized ImpactScope`. Update the Incident
dataclass at lines 104-127 to replace `blast_radius: list[str] | None`
with `impact_scope: ImpactScope | None`. Add a one-line migration note:
"Breaking schema change; no `incidents` rows exist yet so no backfill is
needed."

### AMBIGUITY-1 — `mark_resolved` after the `kind` enum
**Spec lines ~163-175.** With `kind` replacing `resolved`, the spec
doesn't say whether `mark_resolved` sets `kind='fixed'` only, or
`kind='fixed'` AND populates the new Incident's `confirmation_method`.
Add explicit text: "sets `kind='fixed'` on the Finding, sets
`fix_commit_sha`, and creates an Incident with
`detection_method='internal'` and `confirmation_method='fix_commit'`.
Always creates a new Incident row; never upserts."

### AMBIGUITY-2 — pytest plugin's `detection_method` value
**Spec lines ~362-374.** After the Delta-1 split, the plugin must set
both `detection_method` and `confirmation_method`. The synthesis didn't
specify. Add: "When a pytest failure matches an open Finding, the plugin
creates an Incident with `detection_method='test_failure'` and
`confirmation_method='test_failure'`. The two-field repetition is
intentional — the same observation served both roles."

### GAP-1 — `resolved → kind` migration logic missing from Piece 1
**Spec lines ~181-189.** Currently lists two `ALTER TABLE … ADD COLUMN`
calls. Add: "(3) Add `kind TEXT DEFAULT 'opinion'` to `findings`. (4)
Backfill: `UPDATE findings SET kind = CASE WHEN resolved=1 THEN 'fixed'
ELSE 'opinion' END`. (5) On Python ≥3.11 / sqlite ≥3.35: `ALTER TABLE
findings DROP COLUMN resolved`. On older sqlite: 12-step rebuild
(CREATE new → INSERT SELECT → DROP old → RENAME). Wrapped in WAL-mode
test."

### GAP-2 — upsert key still references `resolved=0`
**Spec is silent; codebase reality (`violation_db.py:99-101`).** The
upsert key `(file_path, line_start, line_end, category, resolved=0)`
must change to `kind IN ('opinion','confirmed')`. Add a Piece 1
sub-bullet: "Update `persist_findings` upsert SELECT clause from
`resolved = 0` to `kind IN ('opinion','confirmed')` to prevent duplicate
opinion rows when re-reviewing files with confirmed Findings."

### GAP-3 — validation only walks one bug
**Spec lines ~473-492.** The May-9 retry-storm walkthrough was the only
acceptance run, and it shaped Deltas 1 and 2 directly — sample-of-one
risk. Expand to require two bugs: (1) one the model flagged
(traditional case), (2) one the model missed but where a test failure
would surface (tests the A4 invariant). Add bullet: "If the schema can
capture both shapes without violating A4, the schema is right."

### GAP-4 — reviewer grounding absent from scope boundaries
**Spec lines ~431-453 ("What this does NOT include").** Add subsection:
"**Reviewer Grounding (upstream prerequisite, separate spec).** The
2026-05-09 synthesis recommends shipping schema-constrained Ollama
output + deterministic verbatim validator before Piece 1. Spec TBD
pending architectural decision (see deficit-remediation doc). Without
this, incidents corroborate noise rather than signal."

### DRIFT-1 — `confirming_signal` referenced 4× under the old name
**Spec lines 118, 135, 168, 220.** Global search-and-replace:
`confirming_signal=...` → `detection_method=..., confirmation_method=...`.

## Codebase prep (do before Piece 1, parallelizable)

Three pre-work commits that aren't part of v0.2 itself but unblock it:

### Pre-1 — widen `OllamaClient.response_format` type
**Files:** `processor.py:84,89,131,164`, `extractor.py:200`,
`tests/test_processor.py:705`. Change `Optional[str]` →
`Optional[Union[str, dict]]`. Pure plumbing; no behavior change. ~30min.
Unlocks the grounding spec's schema-constrained output regardless of
which architectural option lands.

### Pre-2 — extract enclosing-symbol-by-line helper
**File:** new helper in `research_agent/tools/import_resolver.py` (it
already does `ast.parse` with `SyntaxError` handling at line 46) —
`def enclosing_symbol(source: str, line: int) -> str | None:`. Walks
`ast.FunctionDef` / `ast.ClassDef`, matches `lineno <= line <=
end_lineno`, returns the innermost name. ~45min + tests. Unlocks Piece 4
pickaxe overlay.

### Pre-3 — promote `ImportResolver` to shared infra
**Currently:** `research_agent/tools/import_resolver.py`, behind
`[research]` extras. **Proposal:** move to
`ollama_sentinel/imports/resolver.py` (or
`ollama_sentinel/context/import_resolver.py` to sit next to other
context infrastructure). Keep a back-compat re-export in
`research_agent/tools/`. ~1h + test path updates.
Unlocks Piece 4 default-install support. **Or** keep where it is and
add a feature-detect-and-degrade path in the pytest plugin — but then
50% of users get a less-effective `suspect_commits`. The promotion is
worth the hour.

## Pattern reuse map (Piece 1 implementation reference)

From the pattern-mapper, ranked by reuse leverage. **Implementers should
read this before writing any new code.**

| New artifact | Closest analog | Strategy |
|---|---|---|
| `Incident` dataclass | `Finding` at `violation_db.py:11-19` + DDL pattern | copy-and-modify, trivial |
| `ImpactScope` JSON column | `_serialize` at `research_agent/utils/cache.py:27-34` | copy-shape, moderate |
| `Finding.kind` state machine | `embed_text` migration at `violation_db.py:58-69` | copy migration recipe; **invent column semantics** (first state machine in repo) |
| `first_detected_revision` | `first_seen` plumbing at `violation_db.py:88` + `git.Repo` at `processor.py:268-273` (`self.repo.head.commit.hexsha`) | extend in place, trivial |
| Schema-constrained Ollama output | `payload["format"]` at `processor.py:131`, `response_format="json"` at `extractor.py:200` | extend in place after Pre-1; trivial |
| Verbatim-excerpt validator | `_parse_finding` at `extractor.py:217-223` | sibling helper, trivial |
| Quote-first prompt ordering | `build_review_context` section list at `recipes.py:48-91` | one new `Section`, trivial |
| Pytest plugin entry point | `[project.scripts]` at `pyproject.toml:56-57` | new TOML block, **first `pytest11` entry** in repo |
| Post-commit hook installer | `git.Repo(...)` at `processor.py:268-273` | reuse construction, moderate |
| Import-graph hop scoring | `_resolve_import_neighbors` at `processor.py:414-457` + `iter_commits` for recency | extend in place after Pre-2 + Pre-3, moderate |
| Pickaxe (`git log -S`) | `self.repo.git.diff(...)` at `processor.py:297-299` (only `repo.git.<verb>` site in repo) | copy call shape, trivial after Pre-2 |
| `Incident` SQL CRUD | `persist_findings` etc. at `violation_db.py:78-181` | copy-and-modify, trivial |
| `incidents` / `confirm` / `install-hooks` CLI | `report` at `cli.py:202-284` | copy-and-modify, trivial |

Two artifacts have no analog: `Finding.kind` state machine and the
pytest plugin entry point. Both are conventional patterns from their
ecosystems — cost is documenting once, not inventing.

## Cross-cutting invariants to preserve

From the pattern map's cross-cutting section:

1. **Pipeline attachment for new findings work** — wire at
   `watcher.py:216-232` (the extract-then-persist seam) wrapped in the
   existing `try/except: log.warning(...)` to preserve the
   "best-effort, never blocks review saving" invariant.
2. **Idempotent migrations** — every new column ALTER and the new
   `incidents` table use the `_migrate` shape at `violation_db.py:52-72`
   (`PRAGMA table_info`, set-difference on column names, `ALTER TABLE` +
   UPDATE inside `try/except sqlite3.DatabaseError`).
3. **Locking on every write** — every new CRUD addition wraps
   `self._conn.execute(...)` in `with self._lock:`. Non-negotiable for
   WAL + threading safety between watcher and dashboard read loops.

## Revised sequencing

```
[DONE] Architectural decision: option 1 (locked 2026-05-09)
[DONE] Reviewer-grounding spec at ../plans/2026-05-09-reviewer-grounding.md
   ↓
Pre-1 (response_format type widening)                        ~30min
Pre-2 (enclosing_symbol helper)                              ~45min
Pre-3 (ImportResolver promotion to shared infra)             ~1h
   └── these three are parallelizable
   ↓
Spec edits 1-8 from above                                    ~1h
   ↓
Reviewer-grounding implementation (Steps 1+2+3)              ~1.5 days
   ↓
v0.2 Piece 1 (revised schema, full migration, upsert fix)    ~full day
   ↓
v0.2 Pieces 2/3/4/5 in parallel as before
   └─ Piece 4 uses the import-graph + pickaxe hybrid (after Pre-2/3)
```

**Net new work added by this remediation:** ~half day (architectural
decision + 3 pre-commits + spec edits) before any v0.2 code lands. **In
exchange:** Piece 1 ships in one full day instead of stalling on
discovered blockers, and the grounding work proceeds on a foundation
that doesn't fight the existing pipeline.

## What this doc does NOT do

- **Does not edit the v0.2 spec.** The eight spec edits above are
  prescriptions for a separate revision pass. Doing them is ~1h of
  mechanical edits.
- **Does not write the reviewer-grounding spec.** Architectural decision
  is upstream of that work.
- **Does not implement the three pre-commits.** Each is small enough to
  ship independently when an implementer picks them up.
- **Does not run the second-bug acceptance walkthrough.** Synthesis
  flagged this; remediation surfaces it as GAP-3; actual walkthrough
  remains a follow-up.

## Ground truth at writing

- master @ `5c1e4a6`, working tree clean.
- Synthesis at `docs/superpowers/research/2026-05-09-v02-incident-memory-research.md`.
- v0.2 spec at `docs/superpowers/plans/2026-05-02-v02-incident-schema.md`,
  status "ready for review, then implementation," zero code landed.
- Three agent transcripts ephemeral under `/private/tmp/claude-501/...`;
  this document is the durable record.
