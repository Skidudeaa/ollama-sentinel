# Findings triage — design

**Date:** 2026-06-03
**Status:** Approved (brainstorm), pending implementation plan
**Slice:** 2 of N in the "make findings actionable" arc (after surface/SARIF, PR #14)

## Problem

`ViolationDB` accumulates findings forever. `mark_resolved` exists but **no CLI
command calls it** — there is no way to close a finding, and no way to list
open findings with the IDs you'd need to act on one. The surface slice made
findings *visible*; this slice makes them *closeable*, and records why each one
closed so the dismiss rate becomes a usable signal of model quality (this
project has known AI-slop issues, so "how often is the model wrong" matters).

> Naming: the slice is internally called "triage," but `triage` is already a
> command (log diagnosis). This slice's commands are **`findings`**,
> **`resolve`**, **`dismiss`** — no collision.

## Scope

Three commands plus the schema + DB methods they need:

- **`findings`** — list open findings with IDs.
- **`resolve <id>`** — mark a finding fixed.
- **`dismiss <id>`** — mark a finding a false-positive / won't-fix.

### Explicitly out of scope (later or never)

- **Stale auto-resolve / `prune`** — its own follow-up slice; it's a
  relocation-driven batch automation (reuses `sarif.relocate_finding`) with its
  own design. The `resolution` column reserves `'stale'` for it.
- **Reopen / unresolve** — YAGNI.
- **Batch ids** (`resolve 42 31 12`) — single id per call, matching `confirm`.
- **Free-form `--note`** — would need another column; the fixed/dismissed
  reason is the signal.
- **Listing resolved findings** (`findings --resolved`) — `findings` lists open
  findings only. Reporting on closed findings is a separate concern.

## Design

### Piece 1 — schema + DB methods (`ollama_sentinel/violation_db.py`)

**Column.** Add one nullable column to the `findings` table, via the same
two-place idempotent pattern used for `verbatim_excerpt`/`embed_text` (add to
`_CREATE_TABLE` for fresh DBs; add to `_migrate` guarded by the existing `cols`
check for legacy DBs):

- `resolution TEXT` — `'fixed' | 'dismissed'` (reserves `'stale'` for the prune
  slice). NULL for open findings and legacy rows.

**`mark_resolved` gains a `resolution` parameter and returns a count.** New
signature:

```
mark_resolved(finding_id, *, fix_commit=None, resolution=None) -> int
```

Behavior:
- Always sets `resolved = 1` for the row with `finding_id`.
- When `resolution` is not None, also sets `resolution = <value>`.
- When `fix_commit` is not None, also sets `fix_commit_sha` and inserts a
  `fix_commit` Incident — unchanged from today. `fix_commit` and `resolution`
  may both be supplied and both apply.
- Returns the number of finding rows updated (`cursor.rowcount`): **0** means no
  finding has that id. Existing callers that ignore the return value are
  unaffected (None → int is backward-safe).

A manual close records lifecycle state on the finding row and creates **no
Incident** (Incidents are for objective corroboration; a human closing a finding
is not corroboration). This matches the existing single-arg `mark_resolved`.

**Two new read methods:**

- `get_finding(finding_id) -> Optional[dict]` — return the single row (any
  `resolved` state) or None. Used by `resolve`/`dismiss` to detect a bad id
  before mutating.
- `get_open_findings(*, severity=None, file_substr=None, limit=50) -> List[dict]`
  — unresolved findings, optionally filtered by exact `severity` and by
  `file_substr` (case-insensitive `LIKE %substr%` on `file_path`), ordered by
  severity rank then `occurrence_count DESC`. Severity rank is a SQL `CASE`:
  critical=4, high=3, medium=2, low=1, else 0. `limit` caps rows.

### Piece 2 — `findings` list command (`ollama_sentinel/cli.py`)

Mirrors the `report` / `incidents` command shape: `_load_config_or_exit`,
`db_path = watch_dir / config.memory.db_path`, friendly
`No violation database found` message + clean exit when the DB is absent,
open + `try/finally` close.

```
ollama-sentinel findings [-c CONFIG] [--severity SEV] [--file SUBSTR]
                         [--limit N] [-f table|json]
```

- Calls `get_open_findings(severity=..., file_substr=..., limit=...)`.
- Corroborated flag: one `get_findings_with_incidents([...paths...])` call; a
  finding id in that set renders a `✓` in a "Corr" column.
- Pure DB read — no file I/O. (Staleness needs file reads + relocation and
  belongs to the prune slice.)
- Table columns: `ID, Sev, Cat, Location (file:line_start), Count, Corr,
  Description (truncated)`. `json` format prints the raw list (include
  `resolution: null` naturally since it's an open-finding row).
- Empty result → green "No open findings." and clean exit.

### Piece 3 — `resolve` / `dismiss` commands (`ollama_sentinel/cli.py`)

Both follow the `confirm` command's structure (single positional id,
`_load_config_or_exit`, no-DB guard, `try/finally` close):

```
ollama-sentinel resolve <finding_id> [-c CONFIG]
ollama-sentinel dismiss <finding_id> [-c CONFIG]
```

- Resolve `db_path`; if absent → red "No violation database found." + exit 1.
- `get_finding(finding_id)`; if None → red
  `No finding with id <n>; nothing to <verb>.` + exit 1.
- Else `mark_resolved(finding_id, resolution='fixed')` (resolve) or
  `resolution='dismissed'` (dismiss).
- Success message:
  - resolve → green `Resolved finding 42 (fixed).`
  - dismiss → green `Dismissed finding 31 (false-positive).`
- Re-resolving an already-closed finding is idempotent (still exit 0; the row
  exists so `get_finding` is non-None and the UPDATE re-applies the same state).

## Data shapes

`ViolationDB` rows are dicts with `id`, `file_path`, `line_start`, `line_end`,
`category`, `severity`, `description`, `verbatim_excerpt`, `occurrence_count`,
`resolved`, `embed_text`, `triggering_commit_sha`, `fix_commit_sha`, and (new)
`resolution`. `get_open_findings` returns `resolved = 0` rows; those always have
`resolution IS NULL`.

## Testing

`tests/test_violation_db.py`:
- `resolution` round-trips: `mark_resolved(id, resolution='fixed')` then read →
  `resolved == 1`, `resolution == 'fixed'`; same for `'dismissed'`.
- `mark_resolved` with no `resolution` leaves `resolution` NULL (backward compat)
  and still resolves.
- `mark_resolved` returns 1 for an existing id, 0 for a missing id.
- `fix_commit` + `resolution` together: both `fix_commit_sha`, `resolution` set,
  and a `fix_commit` Incident created (existing behavior preserved).
- Legacy migration: a pre-column DB opened via `ViolationDB` gains a NULL
  `resolution` column on existing rows.
- `get_finding`: returns the row for a real id, None for a missing id.
- `get_open_findings`: severity filter (exact), file_substr filter
  (case-insensitive substring), ordering (critical before low; within a
  severity, higher `occurrence_count` first), `limit`, and that resolved
  findings are excluded.

`tests/test_cli.py`:
- `findings`: table output shows seeded findings with their ids; `-f json`
  parses to a list; `--severity` and `--file` filter; empty DB → "No open
  findings"; no DB file → "No violation database"; `--limit` caps.
- `resolve <id>`: exit 0, success message, and the finding's `resolution` is
  `'fixed'` and it no longer appears in `get_open_findings`.
- `dismiss <id>`: exit 0, `resolution == 'dismissed'`, leaves the open list.
- `resolve`/`dismiss` on a nonexistent id: exit 1 + "No finding with id".

## Decisions

- **Record the reason (`resolution` column), not a bare flag.** `resolve` and
  `dismiss` are semantically different; collapsing both to `resolved=1` loses
  the false-positive signal this project specifically cares about. One nullable
  column is cheap and mirrors the migration we just did.
- **Manual close is lifecycle, not an Incident.** Incidents corroborate that a
  finding is real; a human resolving/dismissing is the opposite kind of event.
  Keep it on the finding row. (`fix_commit`-driven resolution still makes an
  Incident — a commit is objective evidence — and that path is untouched.)
- **`findings` is a pure DB read.** No file I/O, no staleness column — that
  needs relocation and is the prune slice's job. Keeps `findings` fast and
  consistent with `report`/`incidents`.
- **Single id, no note, open-only, no reopen.** YAGNI; each is a clean future
  add if needed. Matches the `confirm` command's shape.

## Risk

Low. One nullable column (idempotent migration, legacy-safe), one extended
method (backward-compatible — new kwarg, return type None→int), two new read
methods, three CLI commands that mirror existing ones. No file writes, no new
process. The only behavioral subtlety — `mark_resolved` now returns a count and
accepts `resolution` — is additive and covered by tests that also assert the
existing `fix_commit` Incident path is preserved.
