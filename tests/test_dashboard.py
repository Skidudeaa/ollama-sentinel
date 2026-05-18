"""Tests for the dashboard data helpers and main loop."""
import asyncio
import os
import pathlib
import sqlite3
import time
from unittest.mock import MagicMock, patch

from ollama_sentinel.dashboard import (
    OverviewStats,
    ReviewRow,
    ViolationRow,
    compute_overview,
    recent_reviews,
    render_layout,
    run_dashboard,
    suggested_action,
    top_violations,
    watcher_status,
    watcher_status_from_age,
    _overview_panel,
    _patterns_panel,
    _header_panel_v2,
    _footer_panel_v2,
)
from ollama_sentinel.violation_db import Finding, ViolationDB


def _touch(path: pathlib.Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("review")
    os.utime(path, (mtime, mtime))


def _render(renderable, width: int = 120) -> str:
    """Render a Rich renderable to plain text for content assertions."""
    import io
    from rich.console import Console
    c = Console(width=width, record=True, file=io.StringIO())
    c.print(renderable)
    return c.export_text()


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


# ---------------------------------------------------------------------------
# Control Center v2 tests
# ---------------------------------------------------------------------------


class TestWatcherStatus:
    def test_active_within_60s(self):
        now = time.time()
        reviews = [ReviewRow(rel_path="a.md", mtime=now - 30)]
        label, style = watcher_status(reviews, now)
        assert label == "Active"
        assert style == "active"

    def test_idle_between_60s_and_300s(self):
        now = time.time()
        reviews = [ReviewRow(rel_path="a.md", mtime=now - 120)]
        label, style = watcher_status(reviews, now)
        assert label == "Idle"
        assert style == "idle"

    def test_stale_beyond_300s(self):
        now = time.time()
        reviews = [ReviewRow(rel_path="a.md", mtime=now - 600)]
        label, style = watcher_status(reviews, now)
        assert label == "Stale"
        assert style == "stale"

    def test_no_data_when_empty(self):
        now = time.time()
        label, style = watcher_status([], now)
        assert label == "No Data"
        assert style == "no_data"


class TestWatcherStatusFromAge:
    def test_none_age(self):
        assert watcher_status_from_age(None) == ("No Data", "no_data")

    def test_active(self):
        assert watcher_status_from_age(30.0) == ("Active", "active")

    def test_idle(self):
        assert watcher_status_from_age(120.0) == ("Idle", "idle")

    def test_stale(self):
        assert watcher_status_from_age(600.0) == ("Stale", "stale")


class TestComputeOverview:
    def test_empty_data(self):
        now = time.time()
        stats = compute_overview(
            reviews=[], severity_counts={}, hottest=None,
            new_this_week=0, config_path="test.yaml",
            model_name="gemma3", watch_dir="/tmp", db_exists=False, now=now,
        )
        assert stats.total_reviews == 0
        assert stats.newest_review_age_s is None
        assert stats.total_unresolved == 0
        assert stats.hottest_file is None
        assert stats.new_this_week == 0

    def test_populated_data(self):
        now = time.time()
        reviews = [
            ReviewRow(rel_path="a.md", mtime=now - 10),
            ReviewRow(rel_path="b.md", mtime=now - 100),
        ]
        stats = compute_overview(
            reviews=reviews,
            severity_counts={"critical": 2, "high": 5, "medium": 3},
            hottest=("src/auth.py", 4),
            new_this_week=7,
            config_path="sentinel.yaml",
            model_name="deepseek",
            watch_dir="/code",
            db_exists=True,
            now=now,
        )
        assert stats.total_reviews == 2
        assert stats.newest_review_age_s is not None
        assert abs(stats.newest_review_age_s - 10) < 1
        assert stats.total_unresolved == 10
        assert stats.hottest_file == "src/auth.py"
        assert stats.hottest_count == 4
        assert stats.new_this_week == 7


class TestSuggestedAction:
    def test_no_reviews(self):
        stats = OverviewStats(
            total_reviews=0, newest_review_age_s=None, total_unresolved=0,
        )
        assert "first review" in suggested_action(stats).lower()

    def test_critical_findings(self):
        stats = OverviewStats(
            total_reviews=5, newest_review_age_s=10.0, total_unresolved=3,
            severity_counts={"critical": 2}, hottest_file="auth.py", hottest_count=2,
        )
        action = suggested_action(stats)
        assert "critical" in action.lower()
        assert "2" in action

    def test_high_findings(self):
        stats = OverviewStats(
            total_reviews=5, newest_review_age_s=10.0, total_unresolved=3,
            severity_counts={"high": 3}, hottest_file="db.py", hottest_count=3,
        )
        action = suggested_action(stats)
        assert "high" in action.lower()

    def test_all_clear(self):
        stats = OverviewStats(
            total_reviews=5, newest_review_age_s=10.0, total_unresolved=0,
            severity_counts={},
        )
        assert "all clear" in suggested_action(stats).lower()


class TestControlCenterPanels:
    def test_overview_panel_renders(self):
        stats = OverviewStats(
            total_reviews=10, newest_review_age_s=30.0, total_unresolved=5,
            severity_counts={"high": 3, "medium": 2},
            hottest_file="src/app.py", hottest_count=3,
            new_this_week=2,
        )
        panel = _overview_panel(stats)
        assert panel.title == "Overview"

    def test_patterns_panel_empty(self):
        panel = _patterns_panel([])
        assert panel.title == "Patterns"

    def test_patterns_panel_with_data(self):
        rows = [
            ViolationRow(count=3, severity="high", category="bug",
                         file_path="a.py", line_start=10, line_end=10,
                         description="null ref"),
        ]
        panel = _patterns_panel(rows)
        assert "Patterns (1)" in panel.title

    def test_header_v2_renders(self):
        stats = OverviewStats(
            total_reviews=5, newest_review_age_s=30.0, total_unresolved=8,
            config_path="test.yaml", model_name="gemma3",
            watch_dir="/code", db_exists=True,
        )
        panel = _header_panel_v2(stats, time.time())
        assert panel.border_style == "bold cyan"

    def test_footer_v2_renders(self):
        panel = _footer_panel_v2()
        assert panel.border_style == "dim"


class TestRenderLayoutBackwardsCompat:
    def test_old_signature_produces_legacy_layout(self, tmp_path):
        now = time.time()
        layout = render_layout(
            str(tmp_path), tmp_path, tmp_path / "memory.db",
            [], [], now,
        )
        assert layout["header"] is not None
        assert layout["body"] is not None
        assert layout["footer"] is not None

    def test_new_signature_produces_control_center(self, tmp_path):
        now = time.time()
        layout = render_layout(
            str(tmp_path), tmp_path, tmp_path / "memory.db",
            [], [], now,
            config_path="test.yaml",
            model_name="gemma3",
            severity_counts={"high": 2},
        )
        assert layout["header"] is not None
        assert layout["body"]["left"]["overview"] is not None
        assert layout["body"]["right"] is not None


class TestViolationDBNewHelpers:
    def test_count_by_severity(self, tmp_path):
        db = ViolationDB(str(tmp_path / "m.db"))
        try:
            findings = [
                Finding("a.py", 1, 1, "bug", "critical", "crash"),
                Finding("b.py", 2, 2, "style", "low", "naming"),
                Finding("c.py", 3, 3, "perf", "high", "slow"),
            ]
            db.persist_findings("a.py", [findings[0]])
            db.persist_findings("b.py", [findings[1]])
            db.persist_findings("c.py", [findings[2]])
            counts = db.count_by_severity()
            assert counts["critical"] == 1
            assert counts["low"] == 1
            assert counts["high"] == 1
        finally:
            db.close()

    def test_count_new_since(self, tmp_path):
        db = ViolationDB(str(tmp_path / "m.db"))
        try:
            f = Finding("a.py", 1, 1, "bug", "high", "issue")
            db.persist_findings("a.py", [f])
            count = db.count_new_since("2000-01-01T00:00:00")
            assert count == 1
            count = db.count_new_since("2099-01-01T00:00:00")
            assert count == 0
        finally:
            db.close()

    def test_hottest_file(self, tmp_path):
        db = ViolationDB(str(tmp_path / "m.db"))
        try:
            db.persist_findings("a.py", [
                Finding("a.py", 1, 1, "bug", "high", "issue1"),
                Finding("a.py", 2, 2, "bug", "high", "issue2"),
            ])
            db.persist_findings("b.py", [
                Finding("b.py", 1, 1, "style", "low", "naming"),
            ])
            hot = db.hottest_file(limit=1)
            assert hot[0] == ("a.py", 2)
        finally:
            db.close()

    def test_hottest_file_empty_db(self, tmp_path):
        db = ViolationDB(str(tmp_path / "m.db"))
        try:
            assert db.hottest_file() == []
        finally:
            db.close()


class TestBlendedRank:
    def _vr(self, sev, count, fp="f.py", line=1):
        return ViolationRow(count=count, severity=sev, category="bug",
                             file_path=fp, line_start=line, line_end=line,
                             description="d")

    def test_severity_weight_ordering_invariant(self):
        from ollama_sentinel.dashboard import _SEVERITY_WEIGHT
        w = _SEVERITY_WEIGHT
        assert w["critical"] > w["high"] > w["medium"] > w["low"]
        assert w["critical"] > 7 * w["low"]   # one CRIT outranks 7 LOW

    def test_blended_orders_by_weight_times_count(self):
        from ollama_sentinel.dashboard import blended_rank
        rows = [
            self._vr("medium", 15),   # 2*15 = 30
            self._vr("high", 11),     # 4*11 = 44
            self._vr("critical", 4),  # 8*4  = 32
            self._vr("low", 50),      # 1*50 = 50
        ]
        ranked = blended_rank(rows)
        assert [(r.severity, r.count) for r in ranked] == [
            ("low", 50), ("high", 11), ("critical", 4), ("medium", 15)]

    def test_tiebreak_count_then_filepath(self):
        from ollama_sentinel.dashboard import blended_rank
        a = self._vr("high", 5, fp="z.py")   # 20
        b = self._vr("high", 5, fp="a.py")   # 20 -> file asc
        c = self._vr("high", 9, fp="m.py")   # 36
        ranked = blended_rank([a, b, c])
        assert [r.file_path for r in ranked] == ["m.py", "a.py", "z.py"]

    def test_unknown_severity_weight_zero_sorts_last(self):
        from ollama_sentinel.dashboard import blended_rank
        good = self._vr("low", 1)
        bad = self._vr("bogus", 999)
        assert blended_rank([bad, good]) == [good, bad]

    def test_empty_returns_empty(self):
        from ollama_sentinel.dashboard import blended_rank
        assert blended_rank([]) == []
