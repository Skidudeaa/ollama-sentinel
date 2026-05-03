"""Tests for the dashboard data helpers and main loop."""
import asyncio
import os
import pathlib
import sqlite3
import time
from unittest.mock import MagicMock, patch

from ollama_sentinel.dashboard import (
    ReviewRow,
    ViolationRow,
    recent_reviews,
    run_dashboard,
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


def _mock_live() -> MagicMock:
    live = MagicMock()
    live.__enter__ = MagicMock(return_value=live)
    live.__exit__ = MagicMock(return_value=False)
    return live


class TestRunDashboard:
    async def test_exits_immediately_when_shutdown_pre_set(self, tmp_path):
        shutdown = asyncio.Event()
        shutdown.set()
        with patch("ollama_sentinel.dashboard.Live", return_value=_mock_live()):
            await run_dashboard(
                watch_dir=tmp_path,
                reviews_dir=tmp_path / "reviews",
                db_path=tmp_path / "memory.db",
                shutdown=shutdown,
            )

    async def test_cancellable_sleep_exits_quickly_despite_long_refresh(self, tmp_path):
        shutdown = asyncio.Event()

        async def _trigger() -> None:
            await asyncio.sleep(0.05)
            shutdown.set()

        with patch("ollama_sentinel.dashboard.Live", return_value=_mock_live()):
            task = asyncio.create_task(run_dashboard(
                watch_dir=tmp_path,
                reviews_dir=tmp_path / "reviews",
                db_path=tmp_path / "memory.db",
                shutdown=shutdown,
                refresh_s=30.0,
            ))
            await asyncio.gather(_trigger(), asyncio.wait_for(task, timeout=2.0))

    async def test_recent_reviews_exception_does_not_crash_loop(self, tmp_path, monkeypatch):
        shutdown = asyncio.Event()
        call_count = 0

        def _bad_reviews(reviews_dir: pathlib.Path, limit: int) -> list:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("simulated disk error")
            shutdown.set()
            return []

        monkeypatch.setattr("ollama_sentinel.dashboard.recent_reviews", _bad_reviews)
        with patch("ollama_sentinel.dashboard.Live", return_value=_mock_live()):
            await run_dashboard(
                watch_dir=tmp_path,
                reviews_dir=tmp_path / "reviews",
                db_path=tmp_path / "memory.db",
                shutdown=shutdown,
                refresh_s=0.01,
            )
        assert call_count >= 2

    async def test_db_connection_reset_on_query_failure(self, tmp_path, monkeypatch):
        db_path = tmp_path / "memory.db"
        ViolationDB(str(db_path)).close()

        shutdown = asyncio.Event()
        call_count = 0

        def _bad_violations(db: ViolationDB, min_count: int, limit: int) -> list:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise sqlite3.OperationalError("db locked")
            shutdown.set()
            return []

        monkeypatch.setattr("ollama_sentinel.dashboard.top_violations", _bad_violations)
        with patch("ollama_sentinel.dashboard.Live", return_value=_mock_live()):
            await run_dashboard(
                watch_dir=tmp_path,
                reviews_dir=tmp_path / "reviews",
                db_path=db_path,
                shutdown=shutdown,
                refresh_s=0.01,
            )
        assert call_count >= 2
