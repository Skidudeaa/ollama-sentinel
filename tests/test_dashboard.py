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
    _patterns_panel,
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

    def test_footer_v2_renders(self):
        panel = _footer_panel_v2()
        assert panel.border_style == "dim"

    def test_vitals_strip_renders(self):
        from ollama_sentinel.dashboard import _vitals_strip
        stats = OverviewStats(
            total_reviews=5, newest_review_age_s=30.0, total_unresolved=8,
            config_path="test.yaml", model_name="gemma3",
            watch_dir="/code", db_exists=True,
        )
        panel = _vitals_strip(stats, time.time())
        text = _render(panel)
        assert "gemma3" in text
        assert "Active" in text                 # age 30s -> Active
        assert panel.border_style == "bold cyan"

    def test_vitals_strip_handles_empty(self):
        from ollama_sentinel.dashboard import _vitals_strip
        stats = OverviewStats(total_reviews=0, newest_review_age_s=None,
                              total_unresolved=0)
        panel = _vitals_strip(stats, time.time())   # must not raise
        assert "unknown" in _render(panel)
        assert "no DB" in _render(panel)

    def test_severity_banner_shows_counts_and_action(self):
        from ollama_sentinel.dashboard import _severity_banner
        stats = OverviewStats(
            total_reviews=59, newest_review_age_s=30.0, total_unresolved=1230,
            severity_counts={"critical": 74, "high": 104,
                             "medium": 576, "low": 476},
            hottest_file="ErasZoneView.swift", hottest_count=239,
            db_exists=True,
        )
        text = _render(_severity_banner(stats))
        assert "74" in text and "104" in text and "576" in text and "476" in text
        assert "ErasZoneView.swift" in text and "239" in text
        assert "critical" in text.lower()       # from suggested_action
        assert "MED 576" in text
        assert "MEDI" not in text
        assert "CRIT 74" in text and "HIGH 104" in text and "LOW 476" in text

    def test_severity_banner_empty_placeholder(self):
        from ollama_sentinel.dashboard import _severity_banner
        stats = OverviewStats(total_reviews=0, newest_review_age_s=None,
                              total_unresolved=0, db_exists=False)
        assert "no findings" in _render(_severity_banner(stats)).lower()

    def test_severity_banner_all_clear_when_no_unresolved(self):
        from ollama_sentinel.dashboard import _severity_banner
        stats = OverviewStats(total_reviews=10, newest_review_age_s=30.0,
                              total_unresolved=0, db_exists=True)
        assert "all clear" in _render(_severity_banner(stats)).lower()

    def test_severity_banner_no_hottest_file_fallback(self):
        from ollama_sentinel.dashboard import _severity_banner
        stats = OverviewStats(
            total_reviews=5, newest_review_age_s=30.0, total_unresolved=10,
            severity_counts={"high": 5, "medium": 5},
            hottest_file=None, hottest_count=0, db_exists=True,
        )
        text = _render(_severity_banner(stats))
        assert "—" in text          # 🔥 — fallback rendered
        assert "ErasZoneView" not in text

    def test_reviews_rail_compact_and_selection(self):
        from ollama_sentinel.dashboard import _reviews_rail
        now = time.time()
        rows = [ReviewRow(rel_path="Sources/Vinyl/VinylAudioSourceSelector.md",
                          mtime=now - 2760),
                ReviewRow(rel_path="a/b/Left.md", mtime=now - 3000)]
        panel = _reviews_rail(rows, now, selection=0, scroll=0)
        text = _render(panel, width=40)
        assert "46m" in text                         # 2760s -> 46m ago
        assert "VinylAudioSourceSelector" in text    # basename kept
        assert panel.border_style == "blue"
        assert all(len(ln) <= 40 for ln in text.splitlines())

    def test_reviews_rail_empty(self):
        from ollama_sentinel.dashboard import _reviews_rail
        panel = _reviews_rail([], time.time(), selection=-1, scroll=0)
        assert "no reviews" in _render(panel).lower()


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

    def test_new_signature_produces_triage_layout(self, tmp_path):
        now = time.time()
        layout = render_layout(
            str(tmp_path), tmp_path, tmp_path / "memory.db",
            [], [], now,
            config_path="test.yaml", model_name="gemma3",
            severity_counts={"high": 2},
        )
        assert layout["header"] is not None
        assert layout["banner"] is not None
        assert layout["body"]["left"] is not None
        assert layout["body"]["right"] is not None
        assert layout["footer"] is not None


class TestTriageRenderIntegration:
    """End-to-end lock: render_layout's v2 patterns panel shows findings in
    blended (severity*recurrence) order, not input/recurrence-only order."""

    def test_render_layout_patterns_are_blended_ranked(self, tmp_path):
        now = time.time()
        # low x50 -> weight 50 ; critical x1 -> weight 8 ; high x11 -> weight 44
        # blended order must be: low(50), high(44), critical(8)
        violations = [
            ViolationRow(count=1, severity="critical", category="bug",
                         file_path="CritFile.py", line_start=1, line_end=1,
                         description="crit"),
            ViolationRow(count=50, severity="low", category="style",
                         file_path="LowFile.py", line_start=2, line_end=2,
                         description="low"),
            ViolationRow(count=11, severity="high", category="bug",
                         file_path="HighFile.py", line_start=3, line_end=3,
                         description="high"),
        ]
        layout = render_layout(
            str(tmp_path), tmp_path, tmp_path / "memory.db",
            [], violations, now,
            config_path="test.yaml", model_name="gemma3",
            severity_counts={"critical": 1, "high": 11, "low": 50},
        )
        # The panel render_layout actually built for the patterns region:
        panel = layout["body"]["left"].renderable
        out = _render(panel, width=120)
        i_low = out.index("LowFile.py")
        i_high = out.index("HighFile.py")
        i_crit = out.index("CritFile.py")
        assert i_low < i_high < i_crit, out


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


class TestSeverityPalette:
    def test_palette_is_bold_saturated_and_distinct(self):
        from ollama_sentinel.dashboard import _SEVERITY_STYLE
        s = _SEVERITY_STYLE
        assert s["critical"] == "bold red"
        assert s["high"] == "bold yellow"
        assert s["medium"] == "cyan"
        assert s["low"] == "dim"
        assert len(set(s.values())) == 4


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


class TestPatternsSingleLine:
    LONG = ("Fragile auto-collapse on timeout: the timeout collapses "
            "detailExpanded unconditionally and will incorrectly collapse "
            "a different era's detail opened during the override window.")

    def _row(self):
        return ViolationRow(count=11, severity="high", category="bug",
                            file_path="ErasZoneView.swift", line_start=183,
                            line_end=190, description=self.LONG)

    def test_interactive_row_stays_one_line(self):
        from ollama_sentinel.dashboard import _patterns_panel_interactive
        panel = _patterns_panel_interactive([self._row()], selection=-1, scroll=0)
        out = _render(panel, width=80)
        body = [l for l in out.splitlines() if "ErasZoneView.swift" in l]
        assert len(body) == 1                 # description did NOT wrap
        assert "…" in out                     # it was ellipsised

    def test_static_patterns_row_stays_one_line(self):
        from ollama_sentinel.dashboard import _patterns_panel
        panel = _patterns_panel([self._row()])
        out = _render(panel, width=80)
        body = [l for l in out.splitlines() if "ErasZoneView.swift" in l]
        assert len(body) == 1
        assert "…" in out


class TestBuildLayoutWiring:
    def _src(self):
        import inspect
        from ollama_sentinel.dashboard import run_dashboard
        return inspect.getsource(run_dashboard)

    def test_live_path_uses_triage_tree_and_blended_rank(self):
        src = self._src()
        assert "blended_rank(" in src
        assert 'Layout(name="banner"' in src
        assert "_vitals_strip(" in src
        assert "_severity_banner(" in src
        assert "_reviews_rail(" in src
        assert "_overview_panel(" not in src
        assert "_header_panel_v2(" not in src
        assert "_reviews_panel_interactive(" not in src

    def test_detail_mode_path_preserved(self):
        src = self._src()
        assert "Mode.DETAIL" in src and "_detail_panel(" in src
