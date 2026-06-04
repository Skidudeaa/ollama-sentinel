# Stale-prune (`prune`) — design

**Date:** 2026-06-04
**Status:** Approved (brainstorm), pending implementation plan
**Slice:** 4 of N in the "make findings actionable" arc (after surface/SARIF #14, triage #15, remediate #16)

## Problem

A finding stores a `verbatim_excerpt` so it can be re-anchored to its current line by `sarif.relocate_finding`. When the underlying code changes enough that the excerpt no longer locates in the file, `relocate_finding` returns `Relocation(status="stale")`. **Nothing closes these.** `surface` excludes stale findings from the SARIF document and counts them (`sarif.py:271-273`), but never resolves them; `generate_sarif_file` is documented read-only ("stale findings are excluded from the document and counted, never auto-resolved"). `findings` lists raw `resolved=0` rows with no relocation at all. So a finding whose flagged code has been edited away sits open forever — it can't be surfaced (it's a ghost), and it clutters every `findings` listing and severity count.

The three prior slices deliberately deferred this: surface's spec says "Auto-resolving stale findings — `surface` reports stale findings but never mutates DB state"; triage's spec reserves `resolution='stale'` for "its own follow-up slice … a relocation-driven batch automation (reuses `sarif.relocate_finding`)"; remediate's spec lists "stale-prune" as out of scope. This is that slice: **`prune`** is the command that closes stale findings.

## Scope

One command: `ollama-sentinel prune`. It reads every open finding, relocates each by its verbatim excerpt against the current file (reusing `sarif.relocate_finding` and the same content-reading logic as `generate_sarif_file`), selects only those that relocate to `status="stale"`, previews them, and on confirmation closes each via `mark_resolved(id, resolution='stale')` — recording a distinct resolution reason, creating **no** Incident. It never touches source files.

### Explicitly out of scope

- **Reopen / un-prune** — a pruned finding stays closed; there is no inverse verb (matches the arc's no-reopen stance from triage/remediate). If the issue recurs, the next review re-persists it as a fresh finding.
- **Editing source** — `prune` only closes DB rows. Writing into watched code is remediate's job (`fix <id>`); `prune` is the opposite end of the lifecycle.
- **Pruning non-stale findings** — `status="stored"` (empty/legacy excerpt, unverifiable) and `status="relocated"` (still locatable, possibly after drift) are explicitly **left open**. Only `"stale"` is eligible (see Decisions).
- **Auto-prune inside the watcher / on `surface`** — `prune` is an explicit, on-demand command. No automatic closing during `run` or `surface`; both stay read-only as designed. (A future opt-in `surface --prune-stale` could be layered on later, but it is not part of this slice and would carry its own confirm semantics.)
- **Batch by file/severity selection, `--keep`, per-finding interactive y/n** — the unit is "all currently-stale findings"; the gate is one whole-batch confirm. Finer selection is a clean future add.
- **A `pruned_at` / audit-trail column** — `resolution='stale'` on the existing row is the record. No new column.

## Design

### Flow

```
prune [--yes] [-c CONFIG]
  → _load_config_or_exit; watch_dir = Path(config.watch.directory).resolve()
  → db_path = watch_dir / config.memory.db_path
        no DB file → "[yellow]No violation database found. Run some reviews first.[/]" exit 0
  → open ViolationDB; try/finally close
  → stale = collect_stale_findings(db, watch_dir)        # pure-ish: DB read + safe_read, NO writes
        rows = db.get_all_unresolved()
        for r in rows:
            content = safe_read(watch_dir / r["file_path"], watch_dir) if (watch_dir / r["file_path"]).is_file() else None
            content is None (file gone)            → STALE  (the flagged file no longer exists)
            relocate_finding(content, r).status == "stale" → STALE
            status in {"relocated", "stored"}       → keep open, skip
  → empty stale list → "[green]No stale findings to prune.[/]" exit 0
  → render preview table: ID, Sev, Cat, Location (file:line_start), Description
        header: "N stale finding(s) — flagged code no longer locatable:"
  → apply gate (mirrors remediate's `fix`):
        --yes                  → apply without prompt
        TTY, no --yes          → typer.confirm("Prune these N stale finding(s)?")
                                   'n'/default → "[yellow]Aborted; N finding(s) left open.[/]" exit 0
        non-TTY, no --yes      → print table + "(preview only; pass --yes to prune)" exit 0, NO writes
  → for each stale finding: db.mark_resolved(id, resolution="stale")   # no fix_commit → no Incident
  → "[green]Pruned N stale finding(s) (resolution=stale).[/]"
```

### Piece 1 — stale-selection core (`ollama_sentinel/sarif.py`)

Add one read-only helper next to `generate_sarif_file`, reusing the exact content-reading and staleness rule already in that function so `surface` and `prune` agree on what "stale" means.

```
collect_stale_findings(db, watch_dir: pathlib.Path | str) -> list[dict]
```

- `rows = db.get_all_unresolved()`.
- Per row, read current content with the same logic as `generate_sarif_file._content`: `safe_read(watch_dir / file_path, watch_dir)` when `(watch_dir / file_path).is_file()`, else `None`. (`safe_read` returns `""` for missing/unreadable/symlink/traversal and never raises, so the explicit `is_file()` check is what distinguishes "file gone → stale" from "empty-but-present file → goes through relocation," matching `generate_sarif_file`.)
- A `None` content (file gone) → the finding is stale.
- Otherwise `relocate_finding(content, row).status == "stale"` → stale.
- Return the **full row dicts** of the stale findings, in `get_all_unresolved()` order. Pure with respect to the DB and source: it only reads.

> This deliberately duplicates ~8 lines of the relocation loop from `generate_sarif_file` rather than refactoring that function — the two callers want different outputs (a SARIF doc vs. a stale-row list) and bundling a shared-iterator refactor would violate the slice's single-problem discipline. A later cleanup may extract a shared `_relocate_all(db, watch_dir) -> list[tuple[dict, Relocation | None]]` if a third caller appears; out of scope here.

### Piece 2 — `prune` CLI command (`ollama_sentinel/cli.py`)

Mirrors the existing command shape exactly — `_load_config_or_exit`, `db_path = pathlib.Path(config.watch.directory).resolve() / config.memory.db_path`, the friendly no-DB message + clean exit used by `surface`/`findings`/`incidents`, and `try/finally db.close()`. It owns the preview render, the apply gate, and the `mark_resolved` loop.

```
ollama-sentinel prune [-c CONFIG] [--yes/-y]
```

- `--yes/-y` is the only option beyond `--config/-c`, matching `fix`.
- Preview table built with `rich.table.Table` (same construction style as `findings`): columns `ID, Sev, Cat, Location, Description (truncated to 60)`.
- TTY detection reuses the injectable `_is_stdin_tty()` (already used by `triage`); confirm via `typer.confirm(...)` (already used by `init`). Non-TTY without `--yes` prints the preview plus a "(preview only; pass --yes to prune)" line and exits 0 having written nothing.
- The close loop calls `db.mark_resolved(finding_id, resolution="stale")` per row — **no `fix_commit`**, so no Incident is created. Count the rows actually returned `> 0` by `mark_resolved` (defensive; concurrent close would return 0) and report that count.

## Data shapes

`db.get_all_unresolved()` returns dicts with `id`, `file_path` (relative to `watch_dir`), `line_start`, `line_end`, `category`, `severity`, `description`, `verbatim_excerpt`, `occurrence_count`, `resolved`, `embed_text`, `resolution` (NULL on open rows). `relocate_finding(content, row)` returns `Relocation(start_line, end_line, status)` with `status ∈ {relocated, stored, stale}`. `mark_resolved(finding_id, *, fix_commit=None, resolution=None) -> int` sets `resolved=1`, sets `resolution` when given, and inserts a `fix_commit` Incident **only** when `fix_commit is not None` — confirmed at `violation_db.py:240-248`. Its docstring already enumerates `'stale'` as an expected resolution value (`violation_db.py:215`), so no DB change is required: `resolution` is an arbitrary `TEXT` column and the `'stale'` label is already reserved by the triage slice.

## Safety / behavior properties

- **Distinct, non-destructive label.** Pruned findings record `resolution='stale'` — not `'fixed'` (which would falsely claim the issue was corrected) and not `'dismissed'` (which would falsely claim a false-positive). `'stale'` is honest about the ambiguity: the flagged code is gone, by an unknown means. The dismiss-rate signal that triage cares about stays clean because stale closures are a third, separable bucket.
- **Never closes unprompted.** TTY requires an interactive `[y/N]`; non-TTY requires explicit `--yes`; otherwise the command previews and writes nothing. Reuses `_is_stdin_tty()`, matching the arc.
- **Creates no Incident.** A finding vanishing is not corroboration that it was real — the opposite, if anything. `mark_resolved` without `fix_commit` records lifecycle state only, consistent with how `resolve`/`dismiss` close findings.
- **Read-only on source.** `prune` performs zero file writes; it only reads files (to relocate) and updates DB rows. No `safe_write`, no source mutation — that is remediate's domain.
- **Conservative selection (false-negative–aware).** Only `status="stale"` is pruned. A finding that still relocates (`"relocated"`, even after the code moved/refactored within the file) is **kept open**, because the issue may still be live at the new location. A finding with no verifiable excerpt (`"stored"`) is **kept open**, because absence-of-excerpt is not evidence the code changed. `prune` never guesses.

## Testing

Prescribe outcomes; use the existing CLI test mechanism — `typer.testing.CliRunner().invoke(app, [...])`, `monkeypatch.chdir(tmp_path)`, seed via `ViolationDB.persist_findings`, and reopen the DB to assert state (the established pattern in `tests/test_cli.py`: `_seed_one_finding_id`, the `confirm`/`incidents` tests).

`tests/test_sarif.py` — `collect_stale_findings` (tmp_path + real `ViolationDB`, same fixture style as the existing `generate_sarif_file` tests):
- A finding whose excerpt is **absent** from the current file → returned as stale.
- A finding whose flagged **file no longer exists** → returned as stale.
- A finding whose excerpt **still locates** in the file (including a drifted-but-relocatable excerpt — the multiline/reindented cases already exercised for `relocate_finding`) → **not** returned.
- A finding with an **empty excerpt** (`status="stored"`) → **not** returned.
- Mixed set → only the genuinely-stale subset is returned; the function makes no DB writes (assert all rows still `resolved=0` afterward).

`tests/test_cli.py` — `prune`:
- **Happy path (`--yes`):** seed one finding whose excerpt is gone from a real tmp file and one whose excerpt is present; invoke `prune --yes`; assert exit 0, the stale one is `resolved=1, resolution='stale'`, the relocatable one is still `resolved=0` with `resolution IS NULL`, and the source file is byte-for-byte unchanged.
- **No Incident:** after the prune above, `get_incidents_for_finding(stale_id)` is empty (closing a stale finding records lifecycle only).
- **Refactored-but-relocatable is not pruned:** seed a finding whose excerpt has moved to a different line in the file (drift); `prune --yes` leaves it open.
- **Non-TTY without `--yes`:** with `_is_stdin_tty()` forced false (the `triage` tests already monkeypatch this), invoke `prune` (no `--yes`); assert the stale finding is **still** `resolved=0`, exit 0, and the preview/"preview only" text is printed.
- **Nothing to prune:** DB with only relocatable/empty-excerpt findings → green "No stale findings to prune.", exit 0, all rows untouched.
- **No DB file:** friendly "No violation database found." message, clean exit, no crash.

## Decisions

- **`resolution='stale'`, not `'fixed'` / `'dismissed'`.** A stale finding is ambiguous: the flagged code was either legitimately fixed (close it) or moved/refactored elsewhere (closing it is a false-negative — the issue may persist at a location whose excerpt we can't match). Labeling it `'fixed'` over-claims correction; `'dismissed'` over-claims false-positive. `'stale'` names exactly what we know — "excerpt no longer locatable" — and keeps the triage dismiss-rate signal uncontaminated by a third, mechanically-distinct closure reason. The column already accepts it and the docstring already reserves it; zero schema change.
- **Confirm gate by default, `--yes` to apply, non-TTY previews.** `prune` closes findings in bulk and is the only auto-close path that selects its own targets (resolve/dismiss take an explicit id). A whole-batch preview + confirm keeps a human in the loop before any state change and stays scriptable via `--yes`, matching `fix` and the arc's "never silent" stance. Non-TTY without `--yes` previews and writes nothing, so a misfired pipe can't silently mass-close findings.
- **Accept the false-negative tradeoff by scoping to `"stale"` only.** The conservative choice is to prune *only* what we're confident is gone (excerpt absent / file gone) and keep everything still-locatable open. This means a genuinely-fixed-but-still-relocatable finding lingers until `resolve`/`fix` closes it — an acceptable false-positive (a stale-but-visible entry) in exchange for never silently dropping a finding whose code merely moved. Pruning relocatable findings would invert that risk and is rejected.
- **Whole-batch confirm, not per-finding y/n.** The action is uniform ("these are all unlocatable") and low-stakes (no source change, reversible only by re-review which re-persists). One confirm keeps the command terse; per-finding triage is a future refinement, not this slice.
- **Reuse `relocate_finding` + the `generate_sarif_file` content rule; no watcher integration.** `prune` and `surface` must agree on "stale"; sharing the relocation helper and the `is_file()` content guard guarantees that. Keeping `prune` a standalone explicit command (no auto-run in `run`/`surface`) preserves the read-only guarantees those paths already advertise.

## Risk

Low. `prune` writes no source, adds no column, starts no process, and only ever sets `resolved=1, resolution='stale'` on rows it has positively classified as stale via the same relocation logic `surface` already trusts in production. The single real subtlety — the fixed-vs-moved ambiguity — is contained by (1) pruning only `status="stale"`, never relocatable or empty-excerpt findings, (2) a preview + confirm gate that shows exactly which findings will close before any write, and (3) the honest `'stale'` label, which keeps the closure auditable and distinct from `fixed`/`dismissed`. Worst realistic case — a finding that was actually moved (not fixed) gets pruned — closes a row that the next review will re-surface as a fresh finding if the issue still triggers the model; nothing is lost from source, and the `resolution='stale'` marker makes the closure reason inspectable.
