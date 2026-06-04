"""Tests for ollama_sentinel.violation_db persistence layer."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

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

    def test_duplicate_finding_can_persist_from_worker_threads(self, tmp_path):
        db = ViolationDB(str(tmp_path / "memory.db"))
        finding = _make_finding()
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [
                    pool.submit(db.persist_findings, "src/app.py", [finding])
                    for _ in range(8)
                ]
                for future in futures:
                    future.result()

            rows = db.get_unresolved("src/app.py")
            assert len(rows) == 1
            assert rows[0]["occurrence_count"] == 8
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


class TestMigration:
    def test_embed_text_column_added_on_init(self, tmp_path):
        db_path = str(tmp_path / "v.db")
        db = ViolationDB(db_path)
        cur = db._conn.execute("PRAGMA table_info(findings)")
        cols = {row[1] for row in cur.fetchall()}
        assert "embed_text" in cols
        db.close()

    def test_migrate_is_idempotent(self, tmp_path):
        db_path = str(tmp_path / "v.db")
        db1 = ViolationDB(db_path)
        db1.close()
        # Second init should not raise.
        db2 = ViolationDB(db_path)
        cur = db2._conn.execute("PRAGMA table_info(findings)")
        cols = {row[1] for row in cur.fetchall()}
        assert "embed_text" in cols
        db2.close()

    def test_backfill_populates_embed_text_for_existing_rows(self, tmp_path):
        db_path = str(tmp_path / "v.db")
        # Simulate a pre-migration DB by manually creating the old schema.
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                description TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                resolved INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO findings(file_path, line_start, line_end, category, severity, "
            "description, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("a.py", 5, 5, "bug", "high", "null deref", "2026-01-01", "2026-01-01"),
        )
        conn.commit()
        conn.close()

        db = ViolationDB(db_path)
        rows = db._conn.execute("SELECT embed_text FROM findings").fetchall()
        assert rows[0][0] is not None
        assert "null deref" in rows[0][0]
        db.close()


class TestGetAllUnresolved:
    def test_returns_rows_across_files(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        db.persist_findings("a.py", [Finding("a.py", 1, 1, "bug", "low", "x")])
        db.persist_findings("b.py", [Finding("b.py", 2, 2, "perf", "medium", "y")])
        rows = db.get_all_unresolved()
        files = {r["file_path"] for r in rows}
        assert files == {"a.py", "b.py"}
        db.close()


class TestEmbedTextOnInsert:
    def test_new_findings_have_embed_text(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        db.persist_findings("a.py", [
            Finding("a.py", 10, 12, "security", "critical", "plaintext password"),
        ])
        row = db._conn.execute("SELECT embed_text FROM findings WHERE id=1").fetchone()
        assert row[0] is not None
        assert "plaintext password" in row[0]
        assert "[critical]" in row[0]
        db.close()


from ollama_sentinel.context.embeddings import EmbeddingUnavailable


class _MapEmbedder:
    """Fake embedder: text->vector lookup; raises for unknown keys."""
    def __init__(self, mapping):
        self._m = mapping

    async def embed(self, text, *, cache_key=None):
        for needle, vec in self._m.items():
            if needle in text or needle == cache_key:
                return vec
        raise EmbeddingUnavailable(f"no mapping for {text!r}")


class TestGetNeighborsBySimilarity:
    async def test_returns_top_k_by_cosine(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        db.persist_findings("a.py", [
            Finding("a.py", 1, 1, "security", "high", "sql injection via string format"),
            Finding("a.py", 2, 2, "style", "low", "line too long"),
            Finding("a.py", 3, 3, "perf", "medium", "nested loop over items"),
        ])
        embedder = _MapEmbedder({
            "query_vec": [1.0, 0.0, 0.0],
            "sql injection": [1.0, 0.0, 0.0],        # cosine 1.0
            "nested loop": [0.5, 0.5, 0.0],          # cosine 0.707
            "line too long": [0.0, 1.0, 0.0],        # cosine 0.0
        })
        rows = await db.get_neighbors_by_similarity(
            query_text="query_vec", embedder=embedder, k=2,
        )
        assert len(rows) == 2
        descriptions = [r["description"] for r in rows]
        assert descriptions[0] == "sql injection via string format"
        assert descriptions[1] == "nested loop over items"
        db.close()

    async def test_returns_empty_when_embedding_unavailable(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        db.persist_findings("a.py", [Finding("a.py", 1, 1, "bug", "low", "x")])
        # Embedder always raises.
        class _BadEmbedder:
            async def embed(self, text, *, cache_key=None):
                raise EmbeddingUnavailable("down")
        rows = await db.get_neighbors_by_similarity(
            query_text="anything", embedder=_BadEmbedder(), k=10,
        )
        assert rows == []
        db.close()


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """ViolationDB must be usable from multiple threads (asyncio.to_thread pattern)."""

    def test_concurrent_persist_from_threads(self, tmp_path):
        """Two threads can persist findings concurrently without raising."""
        import threading

        db = ViolationDB(str(tmp_path / "threads.db"))
        errors: list[Exception] = []

        def worker(start: int) -> None:
            try:
                for i in range(50):
                    db.persist_findings(
                        "a.py",
                        [_make_finding(line_start=start + i, line_end=start + i)],
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(s,)) for s in (0, 100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        rows = db.get_all_unresolved()
        assert len(rows) == 100
        db.close()

    def test_read_from_different_thread(self, tmp_path):
        """get_unresolved can be called from a thread other than the one that created the DB."""
        import threading

        db = ViolationDB(str(tmp_path / "xthread.db"))
        db.persist_findings("b.py", [_make_finding(file_path="b.py")])

        result: list = []
        errors: list[Exception] = []

        def reader() -> None:
            try:
                result.extend(db.get_unresolved("b.py"))
            except Exception as exc:
                errors.append(exc)

        t = threading.Thread(target=reader)
        t.start()
        t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(result) == 1
        db.close()

    def test_close_is_idempotent(self, tmp_path):
        """Calling close() twice must not raise."""
        db = ViolationDB(str(tmp_path / "close.db"))
        db.close()
        db.close()


# ---------------------------------------------------------------------------
# v0.2 Piece 1 — Incident schema + migration + CRUD
# ---------------------------------------------------------------------------


def _seed_finding_id(db, **overrides) -> int:
    """Persist one finding and return its row id.

    Selects by the finding's full upsert key (not ``[0]``) so it stays
    correct when several findings share a file.
    """
    f = _make_finding(**overrides)
    db.persist_findings(f.file_path, [f])
    row = db._conn.execute(
        """
        SELECT id FROM findings
        WHERE file_path = ? AND line_start = ? AND line_end = ? AND category = ?
        ORDER BY id DESC LIMIT 1
        """,
        (f.file_path, f.line_start, f.line_end, f.category),
    ).fetchone()
    return row[0]


def _make_incident(finding_id: int, **overrides):
    """Build an Incident with sensible defaults, allowing overrides."""
    from ollama_sentinel.violation_db import Incident

    defaults = dict(
        finding_id=finding_id,
        confirming_signal="test_failure",
        confirming_artifact="tests/test_x.py::test_y",
        triggering_commit="abc123",
        suspect_commits=["abc123", "def456"],
        symptom_file="src/other.py",
        symptom_line=55,
        blast_radius=["src/other.py", "src/more.py"],
        fix_commit=None,
        fix_shape=None,
    )
    defaults.update(overrides)
    return Incident(**defaults)


class TestIncidents:
    def test_persist_incident_creates_row(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            fid = _seed_finding_id(db)
            new_id = db.persist_incident(_make_incident(fid))
            assert isinstance(new_id, int) and new_id > 0

            rows = db.get_incidents_for_finding(fid)
            assert len(rows) == 1
            row = rows[0]
            assert row["finding_id"] == fid
            assert row["confirming_signal"] == "test_failure"
            assert row["confirming_artifact"] == "tests/test_x.py::test_y"
            assert row["symptom_line"] == 55
            # JSON-array columns round-trip as Python lists.
            assert row["suspect_commits"] == ["abc123", "def456"]
            assert row["blast_radius"] == ["src/other.py", "src/more.py"]
            assert row["created_at"]
        finally:
            db.close()

    def test_multiple_incidents_same_finding(self, tmp_path):
        """A1: two incidents on one finding are two distinct rows."""
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            fid = _seed_finding_id(db)
            db.persist_incident(_make_incident(fid, confirming_artifact="run-1"))
            db.persist_incident(_make_incident(fid, confirming_artifact="run-2"))

            rows = db.get_incidents_for_finding(fid)
            assert len(rows) == 2
            assert {r["confirming_artifact"] for r in rows} == {"run-1", "run-2"}
        finally:
            db.close()

    def test_incident_requires_valid_finding_id(self, tmp_path):
        """Incidents require a real Finding FK — orphan inserts must fail."""
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            with pytest.raises(sqlite3.IntegrityError):
                db.persist_incident(_make_incident(99999))
        finally:
            db.close()

    def test_get_findings_with_incidents_filters_correctly(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            fid_with = _seed_finding_id(db, file_path="src/a.py")
            _seed_finding_id(db, file_path="src/b.py")  # no incident
            db.persist_incident(_make_incident(fid_with))

            rows = db.get_findings_with_incidents(["src/a.py", "src/b.py"])
            assert len(rows) == 1
            assert rows[0]["id"] == fid_with
            assert rows[0]["file_path"] == "src/a.py"
        finally:
            db.close()

    def test_link_commit_to_findings_updates_open_only(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            open_fid = _seed_finding_id(db, file_path="src/c.py", line_start=1, line_end=2)
            resolved_fid = _seed_finding_id(
                db, file_path="src/c.py", line_start=9, line_end=9
            )
            db.mark_resolved(resolved_fid)

            n = db.link_commit_to_findings("sha789", ["src/c.py"])
            assert n == 1

            open_row = next(
                r for r in db.get_unresolved("src/c.py") if r["id"] == open_fid
            )
            assert open_row["triggering_commit_sha"] == "sha789"

            resolved_row = db._conn.execute(
                "SELECT triggering_commit_sha FROM findings WHERE id = ?",
                (resolved_fid,),
            ).fetchone()
            assert resolved_row[0] is None
        finally:
            db.close()

    def test_mark_resolved_with_fix_commit_creates_incident(self, tmp_path):
        """Behavioral change: fix_commit resolves AND records an Incident."""
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            fid = _seed_finding_id(db)
            db.mark_resolved(fid, fix_commit="fixsha1")

            assert db.get_unresolved("src/app.py") == []
            row = db._conn.execute(
                "SELECT fix_commit_sha FROM findings WHERE id = ?", (fid,)
            ).fetchone()
            assert row[0] == "fixsha1"

            incidents = db.get_incidents_for_finding(fid)
            assert len(incidents) == 1
            assert incidents[0]["confirming_signal"] == "fix_commit"
            assert incidents[0]["fix_commit"] == "fixsha1"
        finally:
            db.close()

    def test_mark_resolved_without_fix_commit_backward_compat(self, tmp_path):
        """Old call shape still works and creates no Incident."""
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            fid = _seed_finding_id(db)
            db.mark_resolved(fid)

            assert db.get_unresolved("src/app.py") == []
            assert db.get_incidents_for_finding(fid) == []
        finally:
            db.close()

    def test_get_recent_incidents_orders_by_created_at_desc(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            fid = _seed_finding_id(db)
            db.persist_incident(_make_incident(fid, confirming_artifact="older"))
            db.persist_incident(_make_incident(fid, confirming_artifact="newer"))

            rows = db.get_recent_incidents(days=30, limit=50)
            assert len(rows) == 2
            # Most recent first (created_at desc, id desc tiebreak).
            assert rows[0]["confirming_artifact"] == "newer"
        finally:
            db.close()

    def test_migration_on_populated_db(self, tmp_path):
        """A7: reopening a populated pre-v0.2 DB migrates without data loss."""
        db_path = str(tmp_path / "v.db")
        db1 = ViolationDB(db_path)
        for i in range(5):
            db1.persist_findings(
                f"src/f{i}.py", [_make_finding(file_path=f"src/f{i}.py")]
            )
        assert len(db1.get_all_unresolved()) == 5
        db1.close()

        # Reopen — triggers _migrate on an existing populated DB.
        db2 = ViolationDB(db_path)
        try:
            assert len(db2.get_all_unresolved()) == 5  # no data loss

            cur = db2._conn.execute("PRAGMA table_info(findings)")
            cols = {row[1] for row in cur.fetchall()}
            assert "triggering_commit_sha" in cols
            assert "fix_commit_sha" in cols

            tbl = db2._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='incidents'"
            ).fetchone()
            assert tbl is not None
        finally:
            db2.close()


class TestVerbatimExcerptPersistence:
    """The finding's verbatim_excerpt must survive persist -> read."""

    def test_persist_stores_verbatim_excerpt(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings(
                "src/app.py",
                [_make_finding(verbatim_excerpt="x = eval(data)")],
            )
            rows = db.get_unresolved("src/app.py")
        finally:
            db.close()
        assert rows[0]["verbatim_excerpt"] == "x = eval(data)"

    def test_migration_adds_column_to_legacy_db(self, tmp_path):
        # A pre-existing DB created WITHOUT verbatim_excerpt (pre-this-feature).
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
            rows = db.get_unresolved("a.py")
        finally:
            db.close()
        assert "verbatim_excerpt" in rows[0]
        assert rows[0]["verbatim_excerpt"] is None  # legacy row: no excerpt

    def test_upsert_keeps_first_excerpt(self, tmp_path):
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings(
                "src/app.py",
                [_make_finding(verbatim_excerpt="first = excerpt()")],
            )
            # Re-persist the SAME finding location/category with a new excerpt.
            db.persist_findings(
                "src/app.py",
                [_make_finding(verbatim_excerpt="second = excerpt()")],
            )
            rows = db.get_unresolved("src/app.py")
        finally:
            db.close()
        assert len(rows) == 1  # upserted, not duplicated
        assert rows[0]["occurrence_count"] == 2
        assert rows[0]["verbatim_excerpt"] == "first = excerpt()"


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

    def test_mark_resolved_already_resolved_rowcount_stays_one(self, tmp_path):
        """Re-resolving an already-resolved finding reports rowcount 1 (SQLite
        updates the row even when the value is unchanged). Documents this
        semantics so callers aren't surprised.
        """
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [_make_finding(file_path="a.py")])
            fid = db.get_unresolved("a.py")[0]["id"]
            db.mark_resolved(fid, resolution="fixed")
            n2 = db.mark_resolved(fid, resolution="dismissed")
        finally:
            db.close()
        assert n2 == 1  # row found; SQLite reports rowcount=1 even if already resolved

    def test_mark_resolved_missing_id_with_fix_commit_returns_zero_no_incident(
        self, tmp_path
    ):
        """fix_commit on a nonexistent finding_id must return 0, not raise IntegrityError,
        and must not create a dangling Incident row.
        """
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            n = db.mark_resolved(424242, fix_commit="abc123")
            incidents = db.get_incidents_for_finding(424242)
        finally:
            db.close()
        assert n == 0
        assert incidents == []


# ---------------------------------------------------------------------------
# Task 1.2: get_finding + get_open_findings
# ---------------------------------------------------------------------------


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

    def test_returns_resolved_finding_too(self, tmp_path):
        """get_finding must return the row regardless of resolved state."""
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [_make_finding(file_path="a.py")])
            fid = db.get_unresolved("a.py")[0]["id"]
            db.mark_resolved(fid, resolution="stale")
            row = db.get_finding(fid)
        finally:
            db.close()
        assert row is not None
        assert row["resolved"] == 1
        assert row["resolution"] == "stale"


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

    def test_both_filters_together(self, tmp_path):
        """severity= and file_substr= both applied simultaneously."""
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("src/auth.py", [
                _make_finding(file_path="src/auth.py", line_start=1,
                              severity="critical", description="auth-critical"),
            ])
            db.persist_findings("src/auth.py", [
                _make_finding(file_path="src/auth.py", line_start=2,
                              severity="low", description="auth-low", category="style"),
            ])
            db.persist_findings("other/util.py", [
                _make_finding(file_path="other/util.py", line_start=1,
                              severity="critical", description="util-critical"),
            ])
            rows = db.get_open_findings(severity="critical", file_substr="src")
        finally:
            db.close()
        assert len(rows) == 1
        assert rows[0]["description"] == "auth-critical"

    def test_unknown_severity_sorts_last(self, tmp_path):
        """Severities not in the CASE expression (ELSE 0) rank below 'low'."""
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            db.persist_findings("a.py", [
                _make_finding(file_path="a.py", line_start=1, severity="low",
                              description="known-low"),
            ])
            db.persist_findings("a.py", [
                _make_finding(file_path="a.py", line_start=2, severity="custom",
                              description="unknown-sev"),
            ])
            rows = db.get_open_findings()
        finally:
            db.close()
        assert rows[0]["severity"] == "low"
        assert rows[-1]["severity"] == "custom"

    def test_ties_broken_by_id_ascending(self, tmp_path):
        """Full ties (same severity + occurrence_count) resolve deterministically
        by id ascending, so --limit truncates a stable, specified subset rather
        than an unspecified one."""
        db = ViolationDB(str(tmp_path / "v.db"))
        try:
            # Insert in DESCENDING line order so insertion order != natural id
            # order would only coincide via the explicit id tiebreak.
            for i in reversed(range(5)):
                db.persist_findings("a.py", [
                    _make_finding(file_path="a.py", line_start=i + 1,
                                  severity="high", category=f"c{i}",
                                  description=f"f{i}"),
                ])
            all_ids = [r["id"] for r in db.get_open_findings()]
            limited = [r["id"] for r in db.get_open_findings(limit=3)]
        finally:
            db.close()
        assert all_ids == sorted(all_ids)
        assert limited == sorted(all_ids)[:3]
