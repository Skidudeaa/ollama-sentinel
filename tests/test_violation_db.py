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
