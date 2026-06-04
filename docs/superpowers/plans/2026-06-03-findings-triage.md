# Findings Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let developers list open findings with IDs and close them — `resolve` (fixed) / `dismiss` (false-positive) — recording why each closed.

**Architecture:** One nullable `resolution` column on the `findings` table, an extended `mark_resolved` (gains a `resolution` kwarg, returns a row count), two new read methods (`get_finding`, `get_open_findings`), and three CLI commands (`findings`, `resolve`, `dismiss`) that mirror the existing `report`/`incidents`/`confirm` commands.

**Tech Stack:** Python 3.10+, SQLite (`ViolationDB`), Typer + Rich (CLI), pytest (`tmp_path`, class-based).

**Spec:** `docs/superpowers/specs/2026-06-03-findings-triage-design.md`

**Branch:** `feat/findings-triage` (already created; the spec commit `c137b5d` is its first commit).

**Stacking:** Three pieces, one PR each, stacked linearly. Pieces 2 and 3 both depend on Piece 1's column + methods.

---

## File Structure

| File | Responsibility | Piece |
|------|----------------|-------|
| `ollama_sentinel/violation_db.py` *(modify)* | `resolution` column + migration; `mark_resolved` gains `resolution`, returns count; new `get_finding`, `get_open_findings` | 1 |
| `ollama_sentinel/cli.py` *(modify)* | `findings` list command; `resolve`/`dismiss` commands + shared `_close_finding` helper | 2, 3 |
| `tests/test_violation_db.py` *(modify)* | resolution persistence, migration, `get_finding`, `get_open_findings` | 1 |
| `tests/test_cli.py` *(modify)* | `findings`, `resolve`, `dismiss` command tests | 2, 3 |
| `CLAUDE.md` + `README.md` *(modify)* | document the three commands | docs |

---

## Piece 1: Schema + DB methods (PR 1)

### Task 1.1: `resolution` column + `mark_resolved` records it and returns a count

**Files:**
- Modify: `ollama_sentinel/violation_db.py` (`_CREATE_TABLE`, `_migrate`, `mark_resolved`)
- Test: `tests/test_violation_db.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_violation_db.py` (it already imports `sqlite3`, `pytest`, and `Finding, ViolationDB`, and defines `_make_finding(**overrides)`):

```python
def _read_row(db, finding_id):
    """Read one findings row as a dict via raw SQL (avoids depending on
    get_finding, which is implemented in a later task)."""
    cur = db._conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,))
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


class TestResolution:
    """resolution column + mark_resolved behavior."""

    def test_mark_resolved_records_fixed(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [_make_finding(file_path="a.py")])
            fid = db.get_unresolved("a.py")[0]["id"]
            n = db.mark_resolved(fid, resolution="fixed")
            row = _read_row(db, fid)
        finally:
            db.close()
        assert n == 1
        assert row["resolved"] == 1
        assert row["resolution"] == "fixed"

    def test_mark_resolved_records_dismissed(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [_make_finding(file_path="a.py")])
            fid = db.get_unresolved("a.py")[0]["id"]
            db.mark_resolved(fid, resolution="dismissed")
            row = _read_row(db, fid)
        finally:
            db.close()
        assert row["resolution"] == "dismissed"

    def test_mark_resolved_no_reason_leaves_null(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [_make_finding(file_path="a.py")])
            fid = db.get_unresolved("a.py")[0]["id"]
            n = db.mark_resolved(fid)
            row = _read_row(db, fid)
        finally:
            db.close()
        assert n == 1
        assert row["resolved"] == 1
        assert row["resolution"] is None

    def test_mark_resolved_missing_id_returns_zero(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            assert db.mark_resolved(424242, resolution="fixed") == 0
        finally:
            db.close()

    def test_mark_resolved_fix_commit_still_creates_incident(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [_make_finding(file_path="a.py")])
            fid = db.get_unresolved("a.py")[0]["id"]
            db.mark_resolved(fid, fix_commit="abc123")
            row = _read_row(db, fid)
            incidents = db.get_incidents_for_finding(fid)
        finally:
            db.close()
        assert row["resolved"] == 1
        assert row["fix_commit_sha"] == "abc123"
        assert len(incidents) == 1
        assert incidents[0]["confirming_signal"] == "fix_commit"

    def test_mark_resolved_fix_commit_and_resolution_together(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [_make_finding(file_path="a.py")])
            fid = db.get_unresolved("a.py")[0]["id"]
            db.mark_resolved(fid, fix_commit="abc123", resolution="fixed")
            row = _read_row(db, fid)
            incidents = db.get_incidents_for_finding(fid)
        finally:
            db.close()
        assert row["fix_commit_sha"] == "abc123"
        assert row["resolution"] == "fixed"
        assert len(incidents) == 1

    def test_migration_adds_resolution_column_to_legacy_db(self, tmp_path):
        p = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(p))
        conn.execute(
            """
            CREATE TABLE findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL, line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL, category TEXT NOT NULL,
                severity TEXT NOT NULL, description TEXT NOT NULL,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                resolved INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "INSERT INTO findings (file_path, line_start, line_end, category, "
            "severity, description, first_seen, last_seen) "
            "VALUES ('a.py', 1, 2, 'bug', 'low', 'd', 't', 't')"
        )
        conn.commit()
        conn.close()

        db = ViolationDB(str(p))  # __init__ runs _migrate
        try:
            row = _read_row(db, 1)
        finally:
            db.close()
        assert "resolution" in row
        assert row["resolution"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_violation_db.py::TestResolution -v`
Expected: FAIL — `mark_resolved()` rejects the `resolution=` kwarg (`TypeError: unexpected keyword argument 'resolution'`).

- [ ] **Step 3: Add the column to the fresh-DB schema**

In `_CREATE_TABLE`, change the `verbatim_excerpt TEXT` line to end with a comma and add the new column:

```python
            embed_text      TEXT,
            verbatim_excerpt TEXT,
            resolution      TEXT
```

- [ ] **Step 4: Add the legacy-DB migration**

In `_migrate`, after the existing `verbatim_excerpt` block and before the final `self._conn.commit()`:

```python
                if "resolution" not in cols:
                    self._conn.execute(
                        "ALTER TABLE findings ADD COLUMN resolution TEXT"
                    )
```

- [ ] **Step 5: Rewrite `mark_resolved`**

Replace the entire `mark_resolved` method with this version (adds `resolution`, returns the row count, preserves the `fix_commit` Incident path):

```python
    def mark_resolved(
        self,
        finding_id: int,
        *,
        fix_commit: Optional[str] = None,
        resolution: Optional[str] = None,
    ) -> int:
        """Set ``resolved=1`` for the given finding; return rows updated.

        Optionally records a ``resolution`` reason ('fixed' | 'dismissed' |
        'stale') and/or a ``fix_commit``. When ``fix_commit`` is provided, also
        sets ``fix_commit_sha`` and inserts an Incident with
        ``confirming_signal='fix_commit'`` (unchanged from v0.2). ``fix_commit``
        and ``resolution`` may both be supplied and both apply. Returns the
        number of finding rows updated — 0 means no finding has that id.
        """
        sets = ["resolved = 1"]
        params: list = []
        if resolution is not None:
            sets.append("resolution = ?")
            params.append(resolution)
        if fix_commit is not None:
            sets.append("fix_commit_sha = ?")
            params.append(fix_commit)
        params.append(finding_id)

        with self._lock:
            cur = self._conn.execute(
                f"UPDATE findings SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
            self._conn.commit()
            rowcount = cur.rowcount

        if fix_commit is not None:
            self.persist_incident(
                Incident(
                    finding_id=finding_id,
                    confirming_signal="fix_commit",
                    confirming_artifact=fix_commit,
                    fix_commit=fix_commit,
                )
            )
        return rowcount
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_violation_db.py -v`
Expected: PASS (new `TestResolution` class + all existing violation_db tests, including any that call the old `mark_resolved` shape).

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add ollama_sentinel/violation_db.py tests/test_violation_db.py
git commit -m "feat(violation_db): record resolution reason; mark_resolved returns count"
```

### Task 1.2: `get_finding` + `get_open_findings`

**Files:**
- Modify: `ollama_sentinel/violation_db.py` (add two read methods near the other read methods, e.g. after `get_unresolved`)
- Test: `tests/test_violation_db.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_violation_db.py`:

```python
class TestGetFinding:
    def test_returns_row_for_real_id(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [_make_finding(file_path="a.py")])
            fid = db.get_unresolved("a.py")[0]["id"]
            row = db.get_finding(fid)
        finally:
            db.close()
        assert row is not None
        assert row["id"] == fid
        assert row["file_path"] == "a.py"

    def test_returns_none_for_missing_id(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            assert db.get_finding(424242) is None
        finally:
            db.close()


class TestGetOpenFindings:
    def test_orders_by_severity_critical_first(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [
                _make_finding(file_path="a.py", line_start=1, severity="low",
                              category="style"),
            ])
            db.persist_findings("a.py", [
                _make_finding(file_path="a.py", line_start=2, severity="critical",
                              category="security"),
            ])
            rows = db.get_open_findings()
        finally:
            db.close()
        assert rows[0]["severity"] == "critical"
        assert rows[-1]["severity"] == "low"

    def test_orders_by_count_within_severity(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            # Same severity; finding A seen 3x, finding B seen 1x.
            for _ in range(3):
                db.persist_findings("a.py", [
                    _make_finding(file_path="a.py", line_start=1, severity="high",
                                  category="bug", description="A"),
                ])
            db.persist_findings("a.py", [
                _make_finding(file_path="a.py", line_start=2, severity="high",
                              category="bug", description="B"),
            ])
            rows = db.get_open_findings()
        finally:
            db.close()
        assert rows[0]["occurrence_count"] == 3
        assert rows[0]["description"] == "A"

    def test_severity_filter(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [
                _make_finding(file_path="a.py", line_start=1, severity="high",
                              description="HighOnly"),
            ])
            db.persist_findings("b.py", [
                _make_finding(file_path="b.py", line_start=2, severity="low",
                              description="LowOnly", category="style"),
            ])
            rows = db.get_open_findings(severity="low")
        finally:
            db.close()
        assert len(rows) == 1
        assert rows[0]["description"] == "LowOnly"

    def test_file_substr_filter_case_insensitive(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("src/App.py", [
                _make_finding(file_path="src/App.py", line_start=1,
                              description="inApp"),
            ])
            db.persist_findings("other.py", [
                _make_finding(file_path="other.py", line_start=2,
                              description="inOther", category="style"),
            ])
            rows = db.get_open_findings(file_substr="app")
        finally:
            db.close()
        assert len(rows) == 1
        assert rows[0]["description"] == "inApp"

    def test_excludes_resolved(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [
                _make_finding(file_path="a.py", line_start=1, description="open"),
            ])
            db.persist_findings("a.py", [
                _make_finding(file_path="a.py", line_start=2, description="closed",
                              category="style"),
            ])
            closed_id = next(
                r["id"] for r in db.get_unresolved("a.py")
                if r["description"] == "closed"
            )
            db.mark_resolved(closed_id, resolution="fixed")
            rows = db.get_open_findings()
        finally:
            db.close()
        descs = {r["description"] for r in rows}
        assert descs == {"open"}

    def test_limit_caps_rows(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            for i in range(3):
                db.persist_findings("a.py", [
                    _make_finding(file_path="a.py", line_start=i + 1,
                                  category=f"c{i}"),
                ])
            rows = db.get_open_findings(limit=2)
        finally:
            db.close()
        assert len(rows) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_violation_db.py::TestGetFinding tests/test_violation_db.py::TestGetOpenFindings -v`
Expected: FAIL — `AttributeError: 'ViolationDB' object has no attribute 'get_finding'`.

- [ ] **Step 3: Add the two read methods**

In `ollama_sentinel/violation_db.py`, add after the `get_unresolved` method:

```python
    def get_finding(self, finding_id: int) -> Optional[dict]:
        """Return the single findings row for *finding_id*, or None.

        Returns the row regardless of resolved state — callers use it to detect
        a bad id before mutating.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM findings WHERE id = ?", (finding_id,)
            )
            rows = self._rows_to_dicts(cur)
        return rows[0] if rows else None

    def get_open_findings(
        self,
        *,
        severity: Optional[str] = None,
        file_substr: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        """Return unresolved findings, filtered and ranked for triage.

        Ordered by severity (critical → low) then ``occurrence_count`` DESC.
        ``severity`` is an exact match; ``file_substr`` is a case-insensitive
        substring of ``file_path``. ``limit`` caps the rows returned.
        """
        clauses = ["resolved = 0"]
        params: list = []
        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)
        if file_substr is not None:
            clauses.append("LOWER(file_path) LIKE ?")
            params.append(f"%{file_substr.lower()}%")
        where = " AND ".join(clauses)
        params.append(limit)
        with self._lock:
            cur = self._conn.execute(
                f"""
                SELECT * FROM findings
                WHERE {where}
                ORDER BY
                    CASE severity
                        WHEN 'critical' THEN 4
                        WHEN 'high'     THEN 3
                        WHEN 'medium'   THEN 2
                        WHEN 'low'      THEN 1
                        ELSE 0
                    END DESC,
                    occurrence_count DESC
                LIMIT ?
                """,
                tuple(params),
            )
            return self._rows_to_dicts(cur)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_violation_db.py -v`
Expected: PASS (TestGetFinding + TestGetOpenFindings + everything prior).

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/violation_db.py tests/test_violation_db.py
git commit -m "feat(violation_db): add get_finding and get_open_findings"
```

---

## Piece 2: `findings` list command (PR 2)

### Task 2.1: `ollama-sentinel findings`

**Files:**
- Modify: `ollama_sentinel/cli.py` (add `findings` command after the `incidents` command, before `surface`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (it has `_make_report_config`, `_seed_db`, `runner`, `json`, and imports `Finding, ViolationDB`):

```python
class TestFindingsCommand:
    """Tests for 'ollama-sentinel findings'."""

    def test_lists_open_findings_with_ids(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed_db(db_path, [
            Finding("src/app.py", 10, 12, "bug", "high", "Null deref risk"),
        ])
        result = runner.invoke(app, ["findings", "--config", str(cfg)])
        assert result.exit_code == 0, result.output
        assert "Null" in result.output and "deref" in result.output
        assert "high" in result.output

    def test_json_format(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed_db(db_path, [Finding("src/app.py", 1, 2, "bug", "high", "d")])
        result = runner.invoke(app, ["findings", "--config", str(cfg), "-f", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["severity"] == "high"

    def test_severity_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = ViolationDB(str(db_path))
        db.persist_findings("a.py", [Finding("a.py", 1, 1, "bug", "high", "HighOnly")])
        db.persist_findings("b.py", [Finding("b.py", 2, 2, "style", "low", "LowOnly")])
        db.close()
        result = runner.invoke(app, ["findings", "--config", str(cfg), "--severity", "low"])
        assert result.exit_code == 0
        assert "LowOnly" in result.output
        assert "HighOnly" not in result.output

    def test_file_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = ViolationDB(str(db_path))
        db.persist_findings("src/app.py", [Finding("src/app.py", 1, 1, "bug", "high", "inApp")])
        db.persist_findings("other.py", [Finding("other.py", 2, 2, "style", "low", "inOther")])
        db.close()
        result = runner.invoke(app, ["findings", "--config", str(cfg), "--file", "app"])
        assert result.exit_code == 0
        assert "inApp" in result.output
        assert "inOther" not in result.output

    def test_empty_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        ViolationDB(str(db_path)).close()
        result = runner.invoke(app, ["findings", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "No open findings" in result.output

    def test_no_db_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        result = runner.invoke(app, ["findings", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "No violation database" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py::TestFindingsCommand -v`
Expected: FAIL — `findings` is not a registered command.

- [ ] **Step 3: Add the `findings` command**

In `ollama_sentinel/cli.py`, after the `incidents` command (ends with its `console.print(table)`) and before the `surface` command:

```python
@app.command()
def findings(
    config_path: str = typer.Option(
        "ollama-sentinel.yaml", "--config", "-c",
        help="Path to configuration file",
    ),
    severity: Optional[str] = typer.Option(
        None, "--severity", help="Filter by exact severity (e.g. high)",
    ),
    file_substr: Optional[str] = typer.Option(
        None, "--file", help="Filter by file-path substring (case-insensitive)",
    ),
    limit: int = typer.Option(
        50, "--limit", "-l", help="Maximum number of findings to show",
    ),
    output_format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table or json",
    ),
):
    """List open (unresolved) findings with their ids for resolve/dismiss."""
    import json as json_mod

    from rich.table import Table

    from .violation_db import ViolationDB

    config = _load_config_or_exit(config_path)
    db_path = pathlib.Path(config.watch.directory).resolve() / config.memory.db_path
    if not db_path.exists():
        console.print(
            "[yellow]No violation database found. Run some reviews first.[/yellow]"
        )
        raise typer.Exit()

    db = ViolationDB(str(db_path))
    try:
        rows = db.get_open_findings(
            severity=severity, file_substr=file_substr, limit=limit,
        )
        corroborated: set = set()
        if rows:
            paths = sorted({r["file_path"] for r in rows})
            corroborated = {
                r["id"] for r in db.get_findings_with_incidents(paths)
            }
    finally:
        db.close()

    if not rows:
        console.print("[green]No open findings.[/green]")
        raise typer.Exit()

    if output_format == "json":
        console.print(json_mod.dumps(rows, indent=2))
        return

    table = Table(title=f"Open findings ({len(rows)})")
    table.add_column("ID", style="bold", width=5)
    table.add_column("Sev", width=9)
    table.add_column("Cat", width=10)
    table.add_column("Location", style="cyan")
    table.add_column("Count", width=6)
    table.add_column("Corr", width=5)
    table.add_column("Description")

    for r in rows:
        table.add_row(
            str(r["id"]),
            r["severity"],
            r["category"],
            f"{r['file_path']}:{r['line_start']}",
            str(r["occurrence_count"]),
            "✓" if r["id"] in corroborated else "",
            (r["description"] or "")[:60],
        )
    console.print(table)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py::TestFindingsCommand -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'findings' command to list open findings with ids"
```

---

## Piece 3: `resolve` / `dismiss` commands (PR 3)

### Task 3.1: `ollama-sentinel resolve` / `dismiss`

**Files:**
- Modify: `ollama_sentinel/cli.py` (add a `_close_finding` module-level helper + the two commands, after the `confirm` command)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (uses the existing `_seed_one_finding_id` helper):

```python
class TestResolveCommand:
    """Tests for 'ollama-sentinel resolve'."""

    def test_resolve_marks_fixed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        fid = _seed_one_finding_id(db_path)
        result = runner.invoke(app, ["resolve", str(fid), "--config", str(cfg)])
        assert result.exit_code == 0, result.output
        db = ViolationDB(str(db_path))
        try:
            row = db.get_finding(fid)
            assert row["resolved"] == 1
            assert row["resolution"] == "fixed"
            assert db.get_open_findings() == []
        finally:
            db.close()

    def test_resolve_missing_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        ViolationDB(str(db_path)).close()
        result = runner.invoke(app, ["resolve", "99999", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "No finding with id" in result.output

    def test_resolve_no_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        result = runner.invoke(app, ["resolve", "1", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "No violation database" in result.output


class TestDismissCommand:
    """Tests for 'ollama-sentinel dismiss'."""

    def test_dismiss_marks_dismissed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        fid = _seed_one_finding_id(db_path)
        result = runner.invoke(app, ["dismiss", str(fid), "--config", str(cfg)])
        assert result.exit_code == 0, result.output
        db = ViolationDB(str(db_path))
        try:
            row = db.get_finding(fid)
            assert row["resolved"] == 1
            assert row["resolution"] == "dismissed"
            assert db.get_open_findings() == []
        finally:
            db.close()

    def test_dismiss_missing_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        ViolationDB(str(db_path)).close()
        result = runner.invoke(app, ["dismiss", "99999", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "No finding with id" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py::TestResolveCommand tests/test_cli.py::TestDismissCommand -v`
Expected: FAIL — `resolve`/`dismiss` are not registered commands.

- [ ] **Step 3: Add the shared helper + the two commands**

In `ollama_sentinel/cli.py`, after the `confirm` command, add:

```python
def _close_finding(
    finding_id: int, config_path: str, *, resolution: str, past: str, tail: str
) -> None:
    """Shared body for resolve/dismiss: validate id, mark_resolved, report.

    ``resolution`` is the stored reason ('fixed'/'dismissed'); ``past`` and
    ``tail`` shape the success line, e.g. "Resolved finding 42 (fixed)."
    """
    from .violation_db import ViolationDB

    config = _load_config_or_exit(config_path)
    db_path = pathlib.Path(config.watch.directory).resolve() / config.memory.db_path
    if not db_path.exists():
        console.print("[red]No violation database found.[/red]")
        raise typer.Exit(code=1)

    db = ViolationDB(str(db_path))
    try:
        if db.get_finding(finding_id) is None:
            console.print(f"[red]No finding with id {finding_id}.[/red]")
            raise typer.Exit(code=1)
        db.mark_resolved(finding_id, resolution=resolution)
    finally:
        db.close()

    console.print(f"[green]{past} finding {finding_id} ({tail}).[/green]")


@app.command()
def resolve(
    finding_id: int = typer.Argument(..., help="ID of the Finding to resolve"),
    config_path: str = typer.Option(
        "ollama-sentinel.yaml", "--config", "-c",
        help="Path to configuration file",
    ),
):
    """Mark a Finding resolved (fixed). Records resolution='fixed'."""
    _close_finding(
        finding_id, config_path, resolution="fixed",
        past="Resolved", tail="fixed",
    )


@app.command()
def dismiss(
    finding_id: int = typer.Argument(..., help="ID of the Finding to dismiss"),
    config_path: str = typer.Option(
        "ollama-sentinel.yaml", "--config", "-c",
        help="Path to configuration file",
    ),
):
    """Dismiss a Finding as a false-positive / won't-fix. Records resolution='dismissed'."""
    _close_finding(
        finding_id, config_path, resolution="dismissed",
        past="Dismissed", tail="false-positive",
    )
```

Note: `typer.Exit(code=1)` raised inside the `try` still runs the `finally` (`db.close()`) before propagating — exit code 1 is preserved.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py::TestResolveCommand tests/test_cli.py::TestDismissCommand -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'resolve' and 'dismiss' commands to close findings"
```

---

## Final: docs

### Task 4.1: Document the three commands

**Files:**
- Modify: `CLAUDE.md` (Build & Run command list; `cli.py` module-table row)
- Modify: `README.md` (Common tasks table)

- [ ] **Step 1: Add to the CLAUDE.md command list**

In `CLAUDE.md`, under "Build & Run", after the `ollama-sentinel surface` line, add:

```
ollama-sentinel findings            # list open Findings with ids (filter: --severity/--file)
ollama-sentinel resolve 42          # close Finding 42 as fixed (resolution='fixed')
ollama-sentinel dismiss 31          # close Finding 31 as false-positive (resolution='dismissed')
```

- [ ] **Step 2: Update the `cli.py` module-table row in CLAUDE.md**

Change the `cli.py` row's verb list to append `findings, resolve, dismiss`:

```
| `ollama_sentinel/cli.py` | Typer CLI: run, review, init, report, triage, dashboard, confirm, incidents, install-hooks, record-commit, surface, findings, resolve, dismiss |
```

- [ ] **Step 3: Add README "Common tasks" rows**

In `README.md`, after the `Surface findings in your editor` row, add:

```
| List open findings | `ollama-sentinel findings`<br>or `… --severity high --file foo.py` | Table with ids, ranked by severity then frequency; `-f json` for machine-readable |
| Close a finding | `ollama-sentinel resolve 42` / `ollama-sentinel dismiss 31` | `resolve` = fixed, `dismiss` = false-positive; records why so the dismiss rate is a usable signal |
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: document findings/resolve/dismiss commands"
```

---

## Self-Review

**Spec coverage:**
- `resolution` column (fresh + legacy migration) → Task 1.1 ✓
- `mark_resolved(resolution=, returns int)`, `fix_commit` path preserved → Task 1.1 ✓
- `get_finding` → Task 1.2 ✓
- `get_open_findings` (severity exact, file substring case-insensitive, severity-then-count ordering, limit, excludes resolved) → Task 1.2 ✓
- `findings` command (filters, table/json, corroborated ✓, no-DB, empty, pure DB read) → Task 2.1 ✓
- `resolve`/`dismiss` (single id, validate via get_finding, resolution recorded, no Incident, friendly messages, exit codes) → Task 3.1 ✓
- Manual close creates no Incident → Task 3.1 (`_close_finding` calls `mark_resolved` with only `resolution=`, never `fix_commit=`) ✓
- Docs → Task 4.1 ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every command has expected output. ✓

**Type consistency:** `mark_resolved(finding_id, *, fix_commit=None, resolution=None) -> int`, `get_finding(finding_id) -> Optional[dict]`, `get_open_findings(*, severity=None, file_substr=None, limit=50) -> List[dict]`, `_close_finding(finding_id, config_path, *, resolution, past, tail)` — names/signatures identical across defining and calling tasks. The `findings` command reads dict keys (`id`, `severity`, `category`, `file_path`, `line_start`, `occurrence_count`, `description`) that `get_open_findings` returns via `_rows_to_dicts`. ✓

**Note for the implementer:** `Finding`'s positional constructor is `(file_path, line_start, line_end, category, severity, description, verbatim_excerpt="")`. The test seeds use that order. `_make_finding(**overrides)` in `test_violation_db.py` defaults `file_path="src/app.py"`, so tests that persist under a different path pass `file_path=` explicitly AND vary `line_start`/`category` so the upsert key differs (otherwise persisting "two" findings just increments one row's `occurrence_count`).
