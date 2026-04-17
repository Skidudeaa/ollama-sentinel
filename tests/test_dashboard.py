"""Tests for the dashboard data helpers."""
import os
import pathlib
import time

from ollama_sentinel.dashboard import (
    ReviewRow,
    ViolationRow,
    recent_reviews,
    top_violations,
)
from ollama_sentinel.violation_db import Finding, ViolationDB


def _touch(path: pathlib.Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("review")
    os.utime(path, (mtime, mtime))


class TestRecentReviews:
    def test_empty_dir_returns_empty(self, tmp_path):
        assert recent_reviews(tmp_path, limit=10) == []

    def test_missing_dir_returns_empty(self, tmp_path):
        assert recent_reviews(tmp_path / "does_not_exist", limit=10) == []

    def test_returns_base_files_sorted_by_mtime_desc(self, tmp_path):
        now = time.time()
        _touch(tmp_path / "a.md", now - 300)
        _touch(tmp_path / "b.md", now - 100)
        _touch(tmp_path / "sub" / "c.md", now - 200)

        rows = recent_reviews(tmp_path, limit=10)
        names = [r.rel_path for r in rows]
        assert names == ["b.md", "sub/c.md", "a.md"]
        assert all(isinstance(r, ReviewRow) for r in rows)

    def test_excludes_versioned_snapshots(self, tmp_path):
        now = time.time()
        _touch(tmp_path / "foo.md", now - 10)
        _touch(tmp_path / "foo_20260101120000.md", now - 5)
        _touch(tmp_path / "foo_20260101120100.md", now - 3)

        rows = recent_reviews(tmp_path, limit=10)
        assert [r.rel_path for r in rows] == ["foo.md"]

    def test_limit_applied(self, tmp_path):
        now = time.time()
        for i in range(5):
            _touch(tmp_path / f"f{i}.md", now - i)
        rows = recent_reviews(tmp_path, limit=2)
        assert len(rows) == 2
        assert [r.rel_path for r in rows] == ["f0.md", "f1.md"]

    def test_ignores_non_md_files(self, tmp_path):
        now = time.time()
        _touch(tmp_path / "a.md", now)
        _touch(tmp_path / "memory.db", now)
        _touch(tmp_path / "notes.txt", now)
        rows = recent_reviews(tmp_path, limit=10)
        assert [r.rel_path for r in rows] == ["a.md"]


class TestTopViolations:
    def test_empty_db(self, tmp_path):
        db = ViolationDB(str(tmp_path / "m.db"))
        try:
            assert top_violations(db, min_count=2, limit=10) == []
        finally:
            db.close()

    def test_returns_recurring_sorted_by_count(self, tmp_path):
        db = ViolationDB(str(tmp_path / "m.db"))
        try:
            # Seed: persist the same finding twice so it increments to count=2
            f = Finding(
                file_path="app/a.py", line_start=10, line_end=10,
                category="security", severity="high", description="SQL injection",
            )
            db.persist_findings("app/a.py", [f])
            db.persist_findings("app/a.py", [f])

            g = Finding(
                file_path="app/b.py", line_start=20, line_end=20,
                category="perf", severity="medium", description="N+1 query",
            )
            db.persist_findings("app/b.py", [g])
            db.persist_findings("app/b.py", [g])
            db.persist_findings("app/b.py", [g])

            rows = top_violations(db, min_count=2, limit=10)
            assert len(rows) == 2
            assert all(isinstance(r, ViolationRow) for r in rows)
            assert rows[0].count == 3  # highest count first
            assert rows[0].category == "perf"
            assert rows[1].count == 2
            assert rows[1].category == "security"
        finally:
            db.close()

    def test_respects_min_count(self, tmp_path):
        db = ViolationDB(str(tmp_path / "m.db"))
        try:
            f = Finding(
                file_path="a.py", line_start=1, line_end=1,
                category="style", severity="low", description="once only",
            )
            db.persist_findings("a.py", [f])  # count=1
            rows = top_violations(db, min_count=2, limit=10)
            assert rows == []
        finally:
            db.close()
