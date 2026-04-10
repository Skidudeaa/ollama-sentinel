"""Tests for ollama_sentinel.violation_db persistence layer."""

import sqlite3

import pytest

from ollama_sentinel.violation_db import Finding, ViolationDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(**overrides) -> Finding:
    """Create a Finding with sensible defaults, allowing field overrides."""
    defaults = dict(
        file_path="src/app.py",
        line_start=10,
        line_end=12,
        category="bug",
        severity="high",
        description="Possible null dereference",
    )
    defaults.update(overrides)
    return Finding(**defaults)


# ---------------------------------------------------------------------------
# DB file creation
# ---------------------------------------------------------------------------


class TestDBCreation:
    def test_db_file_created_on_disk(self, tmp_path):
        db_path = tmp_path / "memory.db"
        db = ViolationDB(str(db_path))
        try:
            assert db_path.exists()
        finally:
            db.close()


# ---------------------------------------------------------------------------
# persist_findings + get_unresolved
# ---------------------------------------------------------------------------


class TestPersistAndRetrieve:
    def test_persist_and_retrieve_three_findings(self, tmp_path):
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            findings = [
                _make_finding(line_start=1, line_end=3, category="bug"),
                _make_finding(line_start=10, line_end=15, category="security"),
                _make_finding(line_start=20, line_end=25, category="style"),
            ]
            db.persist_findings("src/app.py", findings)

            rows = db.get_unresolved("src/app.py")
            assert len(rows) == 3

            # Verify all expected columns are present and correct
            row = rows[0]
            assert row["file_path"] == "src/app.py"
            assert row["line_start"] == 1
            assert row["line_end"] == 3
            assert row["category"] == "bug"
            assert row["severity"] == "high"
            assert row["description"] == "Possible null dereference"
            assert row["occurrence_count"] == 1
            assert row["resolved"] == 0
            assert row["first_seen"] is not None
            assert row["last_seen"] is not None
            assert row["id"] is not None
        finally:
            db.close()

    def test_persist_finding_for_new_file_creates_new_record(self, tmp_path):
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            db.persist_findings("src/app.py", [_make_finding(file_path="src/app.py")])
            db.persist_findings("src/util.py", [_make_finding(file_path="src/util.py")])

            assert len(db.get_unresolved("src/app.py")) == 1
            assert len(db.get_unresolved("src/util.py")) == 1
        finally:
            db.close()

    def test_empty_findings_list_no_error(self, tmp_path):
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            db.persist_findings("src/app.py", [])
            assert db.get_unresolved("src/app.py") == []
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Upsert / occurrence counting
# ---------------------------------------------------------------------------


class TestUpsert:
    def test_duplicate_finding_increments_occurrence_count(self, tmp_path):
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            finding = _make_finding()
            db.persist_findings("src/app.py", [finding])
            first_rows = db.get_unresolved("src/app.py")
            first_last_seen = first_rows[0]["last_seen"]

            # Persist the same finding again
            db.persist_findings("src/app.py", [finding])
            rows = db.get_unresolved("src/app.py")

            assert len(rows) == 1
            assert rows[0]["occurrence_count"] == 2
            assert rows[0]["last_seen"] >= first_last_seen
        finally:
            db.close()


# ---------------------------------------------------------------------------
# get_recurring
# ---------------------------------------------------------------------------


class TestGetRecurring:
    def test_get_recurring_returns_only_high_count(self, tmp_path):
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            once = _make_finding(line_start=1, line_end=2, category="style")
            twice = _make_finding(line_start=10, line_end=12, category="bug")

            db.persist_findings("src/app.py", [once, twice])
            # Persist the "twice" finding again to bump its count
            db.persist_findings("src/app.py", [twice])

            recurring = db.get_recurring(min_count=2)
            assert len(recurring) == 1
            assert recurring[0]["category"] == "bug"
            assert recurring[0]["occurrence_count"] == 2
        finally:
            db.close()


# ---------------------------------------------------------------------------
# get_neighbors_unresolved
# ---------------------------------------------------------------------------


class TestGetNeighborsUnresolved:
    def test_returns_findings_from_multiple_files(self, tmp_path):
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            db.persist_findings(
                "src/app.py",
                [_make_finding(file_path="src/app.py", category="bug")],
            )
            db.persist_findings(
                "src/util.py",
                [_make_finding(file_path="src/util.py", category="security")],
            )

            rows = db.get_neighbors_unresolved(["src/app.py", "src/util.py"])
            assert len(rows) == 2
            file_paths = {r["file_path"] for r in rows}
            assert file_paths == {"src/app.py", "src/util.py"}
        finally:
            db.close()

    def test_empty_file_list_returns_empty(self, tmp_path):
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            assert db.get_neighbors_unresolved([]) == []
        finally:
            db.close()


# ---------------------------------------------------------------------------
# mark_resolved
# ---------------------------------------------------------------------------


class TestMarkResolved:
    def test_resolved_finding_excluded_from_unresolved(self, tmp_path):
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            db.persist_findings("src/app.py", [_make_finding()])
            rows = db.get_unresolved("src/app.py")
            assert len(rows) == 1

            db.mark_resolved(rows[0]["id"])

            assert db.get_unresolved("src/app.py") == []
        finally:
            db.close()
