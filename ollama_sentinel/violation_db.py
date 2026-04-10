"""
SQLite persistence layer for tracking code review findings.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List


@dataclass
class Finding:
    """A single code review finding."""
    file_path: str
    line_start: int
    line_end: int
    category: str    # e.g., "bug", "security", "performance", "style"
    severity: str    # "critical", "high", "medium", "low"
    description: str


class ViolationDB:
    """SQLite-backed store for code review findings with upsert semantics."""

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS findings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path       TEXT    NOT NULL,
            line_start      INTEGER NOT NULL,
            line_end        INTEGER NOT NULL,
            category        TEXT    NOT NULL,
            severity        TEXT    NOT NULL,
            description     TEXT    NOT NULL,
            first_seen      TEXT    NOT NULL,
            last_seen       TEXT    NOT NULL,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            resolved        INTEGER NOT NULL DEFAULT 0
        )
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(self._CREATE_TABLE)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def persist_findings(self, file_path: str, findings: List[Finding]) -> None:
        """Upsert findings for *file_path*.

        If an unresolved row with the same (file_path, line_start, line_end,
        category) already exists, increment its ``occurrence_count`` and
        update ``last_seen``.  Otherwise insert a new row.
        """
        if not findings:
            return

        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.cursor()
        try:
            for f in findings:
                cur.execute(
                    """
                    SELECT id FROM findings
                    WHERE file_path  = ?
                      AND line_start = ?
                      AND line_end   = ?
                      AND category   = ?
                      AND resolved   = 0
                    """,
                    (f.file_path, f.line_start, f.line_end, f.category),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        """
                        UPDATE findings
                        SET occurrence_count = occurrence_count + 1,
                            last_seen       = ?
                        WHERE id = ?
                        """,
                        (now, row[0]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO findings
                            (file_path, line_start, line_end, category,
                             severity, description, first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f.file_path,
                            f.line_start,
                            f.line_end,
                            f.category,
                            f.severity,
                            f.description,
                            now,
                            now,
                        ),
                    )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def mark_resolved(self, finding_id: int) -> None:
        """Set *resolved=1* for the given finding."""
        self._conn.execute(
            "UPDATE findings SET resolved = 1 WHERE id = ?",
            (finding_id,),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_unresolved(self, file_path: str) -> List[dict]:
        """Return all unresolved findings for *file_path*."""
        cur = self._conn.execute(
            "SELECT * FROM findings WHERE file_path = ? AND resolved = 0",
            (file_path,),
        )
        return self._rows_to_dicts(cur)

    def get_neighbors_unresolved(self, file_paths: List[str]) -> List[dict]:
        """Return all unresolved findings for multiple *file_paths*."""
        if not file_paths:
            return []
        placeholders = ", ".join("?" * len(file_paths))
        cur = self._conn.execute(
            f"SELECT * FROM findings WHERE file_path IN ({placeholders}) AND resolved = 0",
            file_paths,
        )
        return self._rows_to_dicts(cur)

    def get_recurring(self, min_count: int = 2, limit: int = 20) -> List[dict]:
        """Return findings with occurrence_count >= *min_count*, ordered desc."""
        cur = self._conn.execute(
            """
            SELECT * FROM findings
            WHERE occurrence_count >= ?
            ORDER BY occurrence_count DESC
            LIMIT ?
            """,
            (min_count, limit),
        )
        return self._rows_to_dicts(cur)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rows_to_dicts(cursor: sqlite3.Cursor) -> List[dict]:
        """Convert raw cursor rows into a list of dicts keyed by column name."""
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
