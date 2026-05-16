# v0.2: Incident Schema — Finding/Incident Split

**Status:** NOT STARTED — cleanly parked, verified untouched at master @ 47f1929 (no incidents table / hooks.py / pytest plugin / CLI verbs / tests; CLAUDE.md "parked" claim accurate). Prerequisite (reviewer-grounding) now SHIPPED, so this is the next real implementation candidate. Audit: docs/superpowers/plans/2026-05-15-implementation-audit.md
**Effort:** ~3-4 days across schema + hooks + pytest plugin + CLI verbs
**Owner:** unassigned
**Prerequisites:** Phase A merged (PR #4). CB-3 shipped.

---

## The problem in one paragraph

The sentinel's memory contains only model opinions. The LLM says something
is concerning; the LLM keeps saying it; `occurrence_count` goes up. That's
the model agreeing with itself N times. It's not knowledge. The schema
cannot represent: which commit introduced the problem, which commit fixed
it, what objective failure confirmed the model's suspicion, where the
failure actually surfaced vs. where the model flagged, or what tests ran
and passed despite the failure. `Finding.resolved` is a single bit — when
it flips, all knowledge of the fix vaporizes. v0.2 fixes this by
splitting the schema into two nouns: Findings (model opinions) and
Incidents (corroborated events with causal structure).

---

## Adversarial cases the schema must handle

These are enumerated first, before the schema, because the schema exists
to handle them. If a proposed schema can't represent one of these cases,
the schema is wrong.

**A1 — Multiple test failures attributing to the same Finding.**
A test at `test_auth.py:47` fails. Another at `test_session.py:112` fails
in the same pytest run. Both failures overlap with the same open Finding
on `auth/session.py:30-45`. The schema must produce two Incident rows
(different symptom locations) referencing the same Finding, not one
Incident with two symptoms crammed in. Each Incident is its own causal
chain; collapsing them loses the blast-radius signal.

**A2 — Finding resolved by one commit, re-opened by a later one.**
Commit `abc123` fixes a Finding. `mark_resolved` fires, Incident created
with `fix_commit=abc123`. Three weeks later, a refactor re-introduces the
same pattern. The model flags a new Finding (same span, same category).
A new test fails. The schema must allow a new Incident on the new Finding
without conflicting with the closed Incident on the old Finding. The old
Finding stays `resolved=1`; a fresh Finding row is inserted (new
`first_seen`); the new Incident references the new Finding ID.

**A3 — Triple corroboration without double-counting.**
A Finding promotes via test failure (Incident #1). The user also runs
`ollama-sentinel confirm 42` on the same Finding (would-be Incident #2).
Later, a fix-shaped commit lands (would-be Incident #3). The schema must
allow all three Incident rows — they carry different `confirming_signal`
values and different `confirming_artifact` data. They are not duplicates;
they are independent corroborating evidence. Downstream Pattern detection
counts *distinct findings with at least one incident*, not *total incident
count*, so triple-corroboration on one Finding counts as one signal, not
three.

**A4 — Test failure with no matching Finding.**
A test fails at `models.py:55`. No open Finding touches that span. The
schema must NOT auto-create a Finding from thin air. Incidents require a
Finding FK. If no Finding exists, no Incident is created. The test failure
is still a test failure; it's just not linked to the sentinel's memory.
The pytest plugin logs a debug message ("no matching Finding for
models.py:55") and moves on. This preserves the invariant that Findings
come from the model and Incidents come from objective events — the two
sources never cross.

**A5 — Concurrent attribution ambiguity.**
A test fails. Two recent commits both touched files in the import-graph
neighborhood of the failure location. Which commit caused it? The schema
must not force a single attribution. `triggering_commit` on the Incident
is the *most recent* commit that touched overlapping files, recorded as a
best-guess. A separate `suspect_commits` list (JSON-serialized in SQLite)
holds all candidates ranked by graph distance × recency. The consumer
(future Pattern detection, future pre-commit surfacing) uses
`suspect_commits[0]` as the primary and the rest as context. Forcing
single-commit attribution when the signal is ambiguous is worse than
recording the ambiguity.

**A6 — Finding spans shift after a commit.**
Commit `def456` adds 10 lines above an existing Finding at
`processor.py:30-45`. The Finding's span is now stale — the flagged code
is at lines 40-55. The schema does NOT auto-update Finding spans. Finding
spans are immutable after creation (they record where the model *looked*,
not where the code *is*). The pytest plugin matches test failures to
Findings using a tolerance window (±5 lines by default, configurable).
If the code drifts beyond the tolerance, the Finding stops matching and
eventually ages out. A new review of the file produces a new Finding at
the correct span.

**A7 — Schema migration on a populated DB.**
A user has 500 Findings in their violation DB. They upgrade to v0.2. The
migration must: create the `incidents` table, add `triggering_commit_sha`
and `fix_commit_sha` columns to `findings` (nullable, backfilled as NULL),
and leave all existing Finding rows untouched. No data loss. No required
user action. The migration is idempotent (safe to run twice). Follows the
existing `_migrate` pattern in `ViolationDB.__init__`.

---

## Schema

### Incident dataclass (new, in `ollama_sentinel/violation_db.py`)

```python
@dataclass
class Incident:
    """A corroborated event linking a Finding to objective evidence.

    Findings are model opinions. Incidents are things that actually happened.
    Each Incident references exactly one Finding and carries the artifact
    that proves the corroboration. Multiple Incidents may reference the same
    Finding (A1, A3). Incidents are never upserted — each row is a distinct
    corroborating event.
    """
    finding_id: int
    confirming_signal: str   # "test_failure" | "manual_confirm" | "fix_commit"
    confirming_artifact: str # pytest output path, commit SHA, or CLI context
    triggering_commit: str | None       # SHA of the commit that introduced/touched
    suspect_commits: list[str] | None   # ranked candidates when attribution is ambiguous (A5)
    symptom_file: str | None            # where the failure actually surfaced
    symptom_line: int | None            # line in symptom_file
    blast_radius: list[str] | None      # all files where failure was observed
    fix_commit: str | None              # SHA of the commit that resolved
    fix_shape: str | None               # short structural description of the fix
```

### Incident SQL table (new)

```sql
CREATE TABLE IF NOT EXISTS incidents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id          INTEGER NOT NULL REFERENCES findings(id),
    confirming_signal   TEXT    NOT NULL,  -- test_failure | manual_confirm | fix_commit
    confirming_artifact TEXT    NOT NULL,
    triggering_commit   TEXT,
    suspect_commits     TEXT,              -- JSON array of SHAs
    symptom_file        TEXT,
    symptom_line        INTEGER,
    blast_radius        TEXT,              -- JSON array of file paths
    fix_commit          TEXT,
    fix_shape           TEXT,
    created_at          TEXT    NOT NULL
)
```

### Finding table additions (migration)

Two nullable columns added to the existing `findings` table:

```sql
ALTER TABLE findings ADD COLUMN triggering_commit_sha TEXT;
ALTER TABLE findings ADD COLUMN fix_commit_sha TEXT;
```

`triggering_commit_sha` is populated by the post-commit hook when a commit
touches files with open Findings. `fix_commit_sha` is populated when
`mark_resolved` is called with a commit SHA argument (new signature).

### Updated `mark_resolved` signature

```python
def mark_resolved(self, finding_id: int, *, fix_commit: str | None = None) -> None:
    """Set resolved=1 and optionally record the fixing commit SHA.

    If fix_commit is provided, also creates an Incident with
    confirming_signal='fix_commit'.
    """
```

This is a **behavioral change** to an existing method. The old signature
`mark_resolved(self, finding_id: int)` still works (fix_commit defaults to
None). But when a commit SHA is available, the method now does two things:
resolves the Finding AND creates an Incident. Flag this in the PR.

---

## Implementation — five pieces

### Piece 1: Schema + migration + CRUD (~half day)

**Files:** `ollama_sentinel/violation_db.py`, `tests/test_violation_db.py`

Add the `Incident` dataclass next to `Finding`. Add `_CREATE_INCIDENTS_TABLE`
DDL. Extend `_migrate` to:
1. Create the `incidents` table if it doesn't exist.
2. Add `triggering_commit_sha` to `findings` if missing.
3. Add `fix_commit_sha` to `findings` if missing.

New methods on `ViolationDB`:

```python
def persist_incident(self, incident: Incident) -> int:
    """Insert an Incident row. Returns the new row ID. Never upserts."""

def get_incidents_for_finding(self, finding_id: int) -> list[dict]:
    """Return all Incidents referencing this Finding."""

def get_recent_incidents(self, *, days: int = 30, limit: int = 50) -> list[dict]:
    """Return recent Incidents across all Findings, ordered by created_at desc."""

def get_findings_with_incidents(self, file_paths: list[str]) -> list[dict]:
    """Return Findings in file_paths that have at least one Incident.
    JOIN findings ON incidents.finding_id = findings.id. Used by the
    pre-commit hook to surface corroborated issues."""

def link_commit_to_findings(self, commit_sha: str, touched_files: list[str]) -> int:
    """Set triggering_commit_sha on all open Findings in touched_files.
    Returns the number of Findings linked. Called by the post-commit hook."""
```

Updated `mark_resolved`:

```python
def mark_resolved(self, finding_id: int, *, fix_commit: str | None = None) -> None:
```

When `fix_commit` is provided: sets `resolved=1`, sets `fix_commit_sha`,
and inserts an Incident with `confirming_signal='fix_commit'`.

**Tests (in `tests/test_violation_db.py`):**

Mirror existing test conventions (class-based, `tmp_path`, `try/finally
db.close()`). New test class `TestIncidents`:

- `test_persist_incident_creates_row` — round-trip insert + query.
- `test_multiple_incidents_same_finding` — A1: two incidents on one finding.
- `test_incident_requires_valid_finding_id` — FK constraint fires.
- `test_get_findings_with_incidents_filters_correctly` — only findings
  with incidents returned.
- `test_link_commit_to_findings_updates_open_only` — resolved findings
  not touched.
- `test_mark_resolved_with_fix_commit_creates_incident` — the behavioral
  change on mark_resolved.
- `test_mark_resolved_without_fix_commit_backward_compat` — old call
  shape still works, no incident created.
- `test_migration_on_populated_db` — A7: create a DB with 5 findings,
  close, reopen (triggers migration), verify incidents table exists
  and findings have the new nullable columns.

### Piece 2: Post-commit hook + `install-hooks` CLI verb (~half day)

**Files:** `ollama_sentinel/hooks.py` (new), `ollama_sentinel/cli.py`,
`tests/test_hooks.py` (new)

New module `ollama_sentinel/hooks.py`:

```python
"""Git hook scripts and installers for ollama-sentinel.

Adapted from the Cairn project's post-commit capture pattern: extract
commit metadata via gitpython, link to open Findings in touched files,
and record the commit SHA on those Findings for future Incident
attribution.
"""

import pathlib
from typing import Optional

import git

from .violation_db import ViolationDB


_POST_COMMIT_HOOK = """\
#!/bin/sh
# Installed by ollama-sentinel install-hooks
# Links commits to open Findings in touched files.
ollama-sentinel record-commit
"""


def install_hooks(repo_path: pathlib.Path) -> list[str]:
    """Install git hooks into repo_path/.git/hooks/.

    Returns a list of hook names that were installed. Existing hooks are
    NOT overwritten — if a post-commit hook already exists, it is left
    alone and the user is warned.
    """

def record_commit(
    repo_path: pathlib.Path,
    db: ViolationDB,
    *,
    commit_sha: Optional[str] = None,
) -> int:
    """Link the most recent commit (or commit_sha) to open Findings.

    1. Resolve the commit via gitpython.
    2. Extract the list of files touched by the commit.
    3. Call db.link_commit_to_findings(sha, touched_files).
    4. Return the number of Findings linked.

    This is the post-commit hook's entry point. Also callable from
    `ollama-sentinel record-commit` CLI verb for manual use.
    """
```

New CLI verbs in `cli.py`:

```python
@app.command()
def install_hooks(...):
    """Install git hooks (post-commit) into the current repo."""

@app.command(name="record-commit")
def record_commit_cmd(...):
    """Link the most recent commit to open Findings. Called by git post-commit hook."""
```

**Tests:** `tests/test_hooks.py`:

- `test_install_hooks_creates_post_commit` — installs into a fresh git repo.
- `test_install_hooks_does_not_overwrite_existing` — existing hook preserved.
- `test_record_commit_links_findings_in_touched_files` — seed findings, make
  a commit, call record_commit, verify `triggering_commit_sha` populated.
- `test_record_commit_skips_resolved_findings` — resolved findings untouched.
- `test_record_commit_no_findings_is_noop` — commit touching files with no
  findings returns 0, no error.

### Piece 3: `ollama-sentinel confirm` CLI verb (~2 hours)

**Files:** `ollama_sentinel/cli.py`, `tests/test_cli.py`

```python
@app.command()
def confirm(
    finding_id: int = typer.Argument(..., help="ID of the Finding to confirm"),
    config_path: str = typer.Option("ollama-sentinel.yaml", "--config", "-c"),
    note: str = typer.Option("", "--note", "-n", help="Optional context for the confirmation"),
):
    """Manually confirm a Finding, promoting it to an Incident.

    Creates an Incident with confirming_signal='manual_confirm'. The Finding
    remains open (confirmation is corroboration, not resolution). Use
    `mark_resolved` or a fix-commit to close the Finding.
    """
```

**Tests:** in `tests/test_cli.py`:

- `test_confirm_creates_incident` — seed a finding, run confirm, query
  incidents table.
- `test_confirm_nonexistent_finding_errors` — error message, exit code 1.
- `test_confirm_with_note` — note appears in confirming_artifact.

### Piece 4: pytest plugin skeleton (~half day)

**Files:** `ollama_sentinel/pytest_plugin.py` (new), `pyproject.toml`
(entry point), `tests/test_pytest_plugin.py` (new)

The plugin registers via `pyproject.toml`:

```toml
[project.entry-points."pytest11"]
ollama_sentinel = "ollama_sentinel.pytest_plugin"
```

Plugin behavior:

1. On `pytest_sessionfinish`, iterate `session.testsfailed`.
2. For each failed test, extract file path and line number from the
   test's `longrepr` (the traceback).
3. Query the ViolationDB for open Findings whose `(file_path, line_start,
   line_end)` overlaps the failure location within a ±5 line tolerance
   window (A6).
4. For each matching Finding, create an Incident with
   `confirming_signal='test_failure'` and `confirming_artifact` pointing
   to the test node ID.
5. If git is available, populate `triggering_commit` with `HEAD` SHA and
   `suspect_commits` with the last N commits that touched files in the
   Finding's 1-hop import neighborhood (using `ImportResolver` if
   available, falling back to the single file).

**Critical constraint:** the plugin must be zero-cost when disabled. If no
`ollama-sentinel.yaml` exists in the working directory, the plugin does
nothing. If the ViolationDB doesn't exist, the plugin does nothing. No
import-time side effects. No test-time overhead unless actively linked.

Configuration: opt-in via `pytest.ini` / `pyproject.toml`:

```ini
[tool:pytest]
ollama_sentinel = true
ollama_sentinel_config = ollama-sentinel.yaml
ollama_sentinel_tolerance = 5
```

**Tests:** `tests/test_pytest_plugin.py`:

- `test_plugin_creates_incident_on_matching_failure` — seed a finding,
  run a subprocess pytest that fails at the right line, verify incident
  created.
- `test_plugin_skips_when_no_matching_finding` — A4: failure at a line
  with no finding → no incident, debug log emitted.
- `test_plugin_tolerance_window` — A6: finding at line 30, failure at
  line 33, tolerance=5 → match. Failure at line 40 → no match.
- `test_plugin_noop_without_config` — no config file → plugin does
  nothing, no error.
- `test_plugin_multiple_failures_same_finding` — A1: two failures
  matching one finding → two incidents.

### Piece 5: `incidents` subcommand + docs (~2 hours)

**Files:** `ollama_sentinel/cli.py`, `CLAUDE.md`, `docs/VISION.md`,
`README.md`

```python
@app.command()
def incidents(
    config_path: str = typer.Option("ollama-sentinel.yaml", "--config", "-c"),
    days: int = typer.Option(30, "--days", "-d", help="Look back N days"),
    finding_id: Optional[int] = typer.Option(None, "--finding", "-f", help="Filter by finding ID"),
    output_format: str = typer.Option("table", "--format", help="table or json"),
):
    """Show recent Incidents — corroborated events linked to Findings."""
```

Update VISION.md: the v0.2 section changes from aspirational to
descriptive. Strip the test count. Update the Finding/Incident description
to match the shipped schema.

Update CLAUDE.md: new "Recent landings" entry. Add `incidents`, `confirm`,
`install-hooks`, `record-commit` to the CLI command grid. Update the
architecture data flow diagram to show the Incident path alongside the
Finding path.

---

## What this does NOT include

- **Pre-commit hook.** The vision doc mentions surfacing incidents at
  pre-commit time. That's v0.2.1. Get the data model right first; add
  the intervention surface second.
- **Pattern detection.** The "≥3 incidents with same shape → guardrail"
  promotion is v0.3. The schema supports it (query incidents grouped by
  finding category + file neighborhood) but the detection logic is not
  in scope.
- **Reverse import-graph blame attribution.** The vision doc's "post-test:
  run reverse import-graph traversal to attribute blame" is the novel
  capability. It's partially in scope via the pytest plugin's
  `suspect_commits` population, but the full traversal (walk N commits
  back through the import graph, rank by distance × recency) is v0.2.1.
  The pytest plugin populates `suspect_commits` with a simpler heuristic:
  last 5 commits that touched the failing file or its direct imports.
- **Embedding Incidents.** Incidents should eventually get `embed_text`
  columns and participate in semantic recall alongside Findings. That's
  the payoff from the Phase B/C embedding work. Not in v0.2.
- **ImpactItem unification.** The vision doc's v0.3 plan to unify
  Finding and ImpactItem under a shared Incident schema is deliberately
  deferred. Ship the Finding/Incident split first; unify later.

---

## Ordering and dependencies

```
Piece 1 (schema + migration + CRUD)
   ↓ [all other pieces depend on this]
Piece 2 (post-commit hook)     — independent of 3, 4, 5
Piece 3 (confirm CLI verb)     — independent of 2, 4, 5
Piece 4 (pytest plugin)        — independent of 2, 3, 5
Piece 5 (incidents CLI + docs) — depends on 1 only; do last
```

Pieces 2, 3, 4 are parallel after Piece 1 ships. Piece 5 is a
bookend. Total: 5 sequential steps if solo, 3 steps if parallelized
(1 → {2,3,4} → 5).

---

## Validation — the real-bug test

Before this spec ships to an implementation team, take one real bug from
somaCURA or Song Expanse's git history. Manually fill out a Finding row
and an Incident row for it:

- What was the Finding? (file, span, category, severity, description)
- What was the triggering commit? (SHA)
- What was the confirming signal? (test failure? runtime exception? manual?)
- What was the confirming artifact? (traceback? commit message? your memory?)
- What was the symptom file and line? (where did it actually break?)
- What was the blast radius? (what else broke?)
- What was the fix commit? (SHA)
- What was the fix shape? (null check? race condition guard? schema change?)

If the schema can capture it cleanly, the schema is right. If any field
feels forced or missing, the schema is wrong and needs revision before
implementation starts.

This exercise is not optional. It's the spec's acceptance test.

---

## Ground truth at the time this spec was written

- Phase A merged as PR #4 (11 commits, 378 tests, 15 skipped).
- CB-3 shipped (commits f426a55..b9f7968, 6 tests added).
- Structural recall wired earlier this session (5 tests added).
- `findings` table: 12 columns, upsert key is
  `(file_path, line_start, line_end, category, resolved=0)`.
- `mark_resolved(self, finding_id: int)` — current signature, no commit
  SHA parameter.
- `ViolationDB._migrate` — existing pattern for idempotent column adds.
- `gitpython>=3.1.40` — already a core dependency.
- `ImportResolver` — lives in `research_agent/tools/`, imported by
  sentinel via `try/except` (structural recall). Not promoted to shared
  infra yet (v0.3).
- No `incidents` table exists.
- No git hooks exist.
- No pytest plugin exists.
