"""
SQLite persistence layer for tracking code review findings.
"""
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional


@dataclass
class Finding:
    """A single code review finding."""
    file_path: str
    line_start: int
    line_end: int
    category: str    # e.g., "bug", "security", "performance", "style"
    severity: str    # "critical", "high", "medium", "low"
    description: str
    verbatim_excerpt: str = ""


@dataclass
class Incident:
    """A corroborated event linking a Finding to objective evidence.

    Findings are model opinions. Incidents are things that actually happened.
    Each Incident references exactly one Finding and carries the artifact
    that proves the corroboration. Multiple Incidents may reference the same
    Finding; Incidents are never upserted — each row is a distinct event.
    """
    finding_id: int
    confirming_signal: str    # "test_failure" | "manual_confirm" | "fix_commit"
    confirming_artifact: str  # pytest node id, commit SHA, or CLI context
    triggering_commit: Optional[str] = None
    suspect_commits: Optional[List[str]] = None  # ranked candidates (ambiguous attribution)
    symptom_file: Optional[str] = None
    symptom_line: Optional[int] = None
    blast_radius: Optional[List[str]] = None     # all files where failure surfaced
    fix_commit: Optional[str] = None
    fix_shape: Optional[str] = None


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
            resolved        INTEGER NOT NULL DEFAULT 0,
            embed_text      TEXT,
            verbatim_excerpt TEXT
        )
    """

    _CREATE_INCIDENTS_TABLE = """
        CREATE TABLE IF NOT EXISTS incidents (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            finding_id          INTEGER NOT NULL REFERENCES findings(id),
            confirming_signal   TEXT    NOT NULL,
            confirming_artifact TEXT    NOT NULL,
            triggering_commit   TEXT,
            suspect_commits     TEXT,
            symptom_file        TEXT,
            symptom_line        INTEGER,
            blast_radius        TEXT,
            fix_commit          TEXT,
            fix_shape           TEXT,
            created_at          TEXT    NOT NULL
        )
    """

    def __init__(self, db_path: str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute(self._CREATE_TABLE)
            self._conn.execute(self._CREATE_INCIDENTS_TABLE)
            self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Idempotent migration: add columns introduced after the initial schema (embed_text backfill, triggering_commit_sha, fix_commit_sha, verbatim_excerpt)."""
        try:
            with self._lock:
                cur = self._conn.execute("PRAGMA table_info(findings)")
                cols = {row[1] for row in cur.fetchall()}
                if "embed_text" not in cols:
                    self._conn.execute("ALTER TABLE findings ADD COLUMN embed_text TEXT")
                    self._conn.execute(
                        """
                        UPDATE findings
                        SET embed_text =
                            '[' || severity || '] ' || category || ' at ' ||
                            file_path || ':' || line_start || ': ' || description
                        WHERE embed_text IS NULL
                        """
                    )
                    self._conn.commit()
                if "triggering_commit_sha" not in cols:
                    self._conn.execute(
                        "ALTER TABLE findings ADD COLUMN triggering_commit_sha TEXT"
                    )
                if "fix_commit_sha" not in cols:
                    self._conn.execute(
                        "ALTER TABLE findings ADD COLUMN fix_commit_sha TEXT"
                    )
                if "verbatim_excerpt" not in cols:
                    self._conn.execute(
                        "ALTER TABLE findings ADD COLUMN verbatim_excerpt TEXT"
                    )
                self._conn.commit()
        except sqlite3.DatabaseError as e:
            import logging
            logging.getLogger("ollama-sentinel").error("ViolationDB migration failed: %s", e)

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
        with self._lock:
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
                        embed_text = (
                            f"[{f.severity}] {f.category} at {f.file_path}:{f.line_start}: {f.description}"
                        )
                        cur.execute(
                            """
                            INSERT INTO findings
                                (file_path, line_start, line_end, category,
                                 severity, description, first_seen, last_seen,
                                 embed_text, verbatim_excerpt)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                                embed_text,
                                f.verbatim_excerpt,
                            ),
                        )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def mark_resolved(
        self, finding_id: int, *, fix_commit: Optional[str] = None
    ) -> None:
        """Set ``resolved=1`` for the given finding.

        Behavioral change (v0.2): when ``fix_commit`` is provided, also
        records ``fix_commit_sha`` on the finding and inserts an Incident
        with ``confirming_signal='fix_commit'``. The old single-argument
        call shape is unchanged and creates no Incident.
        """
        with self._lock:
            if fix_commit is None:
                self._conn.execute(
                    "UPDATE findings SET resolved = 1 WHERE id = ?",
                    (finding_id,),
                )
                self._conn.commit()
                return
            self._conn.execute(
                "UPDATE findings SET resolved = 1, fix_commit_sha = ? WHERE id = ?",
                (fix_commit, finding_id),
            )
            self._conn.commit()
        self.persist_incident(
            Incident(
                finding_id=finding_id,
                confirming_signal="fix_commit",
                confirming_artifact=fix_commit,
                fix_commit=fix_commit,
            )
        )

    def persist_incident(self, incident: Incident) -> int:
        """Insert an Incident row and return its new id. Never upserts."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO incidents
                        (finding_id, confirming_signal, confirming_artifact,
                         triggering_commit, suspect_commits, symptom_file,
                         symptom_line, blast_radius, fix_commit, fix_shape,
                         created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        incident.finding_id,
                        incident.confirming_signal,
                        incident.confirming_artifact,
                        incident.triggering_commit,
                        json.dumps(incident.suspect_commits)
                        if incident.suspect_commits is not None
                        else None,
                        incident.symptom_file,
                        incident.symptom_line,
                        json.dumps(incident.blast_radius)
                        if incident.blast_radius is not None
                        else None,
                        incident.fix_commit,
                        incident.fix_shape,
                        now,
                    ),
                )
                self._conn.commit()
                # lastrowid is always set after a successful single INSERT.
                return int(cur.lastrowid) if cur.lastrowid is not None else -1
            except Exception:
                self._conn.rollback()
                raise

    def link_commit_to_findings(
        self, commit_sha: str, touched_files: List[str]
    ) -> int:
        """Set ``triggering_commit_sha`` on open findings in ``touched_files``.

        Returns the number of findings linked. Resolved findings are skipped.
        """
        if not touched_files:
            return 0
        placeholders = ", ".join("?" * len(touched_files))
        with self._lock:
            cur = self._conn.execute(
                f"""
                UPDATE findings
                SET triggering_commit_sha = ?
                WHERE file_path IN ({placeholders}) AND resolved = 0
                """,
                (commit_sha, *touched_files),
            )
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def _incident_rows(self, cur: sqlite3.Cursor) -> List[dict]:
        """Rows → dicts with JSON-array columns decoded back to lists."""
        rows = self._rows_to_dicts(cur)
        for r in rows:
            for key in ("suspect_commits", "blast_radius"):
                if r.get(key) is not None:
                    r[key] = json.loads(r[key])
        return rows

    def get_incidents_for_finding(self, finding_id: int) -> List[dict]:
        """Return all Incidents referencing this Finding, oldest first."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM incidents WHERE finding_id = ? ORDER BY id ASC",
                (finding_id,),
            )
            return self._incident_rows(cur)

    def get_recent_incidents(
        self, *, days: int = 30, limit: int = 50
    ) -> List[dict]:
        """Return recent Incidents across all Findings, newest first."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT * FROM incidents
                WHERE created_at >= ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (cutoff, limit),
            )
            return self._incident_rows(cur)

    def get_findings_with_incidents(
        self, file_paths: List[str]
    ) -> List[dict]:
        """Return Findings in ``file_paths`` that have ≥1 Incident."""
        if not file_paths:
            return []
        placeholders = ", ".join("?" * len(file_paths))
        with self._lock:
            cur = self._conn.execute(
                f"""
                SELECT DISTINCT findings.* FROM findings
                JOIN incidents ON incidents.finding_id = findings.id
                WHERE findings.file_path IN ({placeholders})
                """,
                file_paths,
            )
            return self._rows_to_dicts(cur)

    def get_unresolved(self, file_path: str) -> List[dict]:
        """Return all unresolved findings for *file_path*."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM findings WHERE file_path = ? AND resolved = 0",
                (file_path,),
            )
            return self._rows_to_dicts(cur)

    def get_all_unresolved(self) -> List[dict]:
        """Return every unresolved finding across all files."""
        with self._lock:
            cur = self._conn.execute("SELECT * FROM findings WHERE resolved = 0")
            return self._rows_to_dicts(cur)

    def get_neighbors_unresolved(self, file_paths: List[str]) -> List[dict]:
        """Return all unresolved findings for multiple *file_paths*."""
        if not file_paths:
            return []
        placeholders = ", ".join("?" * len(file_paths))
        with self._lock:
            cur = self._conn.execute(
                f"SELECT * FROM findings WHERE file_path IN ({placeholders}) AND resolved = 0",
                file_paths,
            )
            return self._rows_to_dicts(cur)

    def get_recurring(self, min_count: int = 2, limit: int = 20) -> List[dict]:
        """Return findings with occurrence_count >= *min_count*, ordered desc."""
        with self._lock:
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

    def count_by_severity(self) -> dict:
        """Return unresolved finding counts grouped by severity."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT severity, COUNT(*) FROM findings "
                "WHERE resolved = 0 GROUP BY severity"
            )
            return dict(cur.fetchall())

    def count_new_since(self, since_iso: str) -> int:
        """Count unresolved findings with first_seen >= *since_iso*."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM findings WHERE first_seen >= ? AND resolved = 0",
                (since_iso,),
            )
            return cur.fetchone()[0]

    def hottest_file(self, limit: int = 1) -> List[tuple]:
        """Top files by unresolved finding count. Returns [(file_path, count)]."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT file_path, COUNT(*) as cnt FROM findings "
                "WHERE resolved = 0 GROUP BY file_path "
                "ORDER BY cnt DESC LIMIT ?",
                (limit,),
            )
            return cur.fetchall()

    async def get_neighbors_by_similarity(
        self,
        query_text: str,
        embedder,
        k: int = 10,
    ) -> List[dict]:
        """Rank all unresolved findings by cosine similarity to query_text.

        `embedder` is duck-typed (OllamaEmbedder or any object with an async
        `embed(text, *, cache_key=None) -> list[float]`). Returns [] if the
        embedder cannot embed the query.
        """
        import asyncio
        import hashlib
        import math
        from ollama_sentinel.context.embeddings import EmbeddingUnavailable

        rows = self.get_all_unresolved()
        if not rows:
            return []

        query_key = f"query:{hashlib.sha256(query_text.encode('utf-8')).hexdigest()}"
        try:
            query_vec = await embedder.embed(query_text, cache_key=query_key)
        except EmbeddingUnavailable:
            return []

        async def _embed_row(row):
            embed_text = row.get("embed_text") or (
                f"[{row['severity']}] {row['category']} at {row['file_path']}:"
                f"{row['line_start']}: {row['description']}"
            )
            try:
                vec = await embedder.embed(embed_text, cache_key=f"finding:{row['id']}")
            except EmbeddingUnavailable:
                vec = None
            return row, vec

        pairs = await asyncio.gather(*(_embed_row(r) for r in rows))
        scored = []
        for row, vec in pairs:
            if vec is None:
                continue
            dot = sum(a * b for a, b in zip(query_vec, vec))
            na = math.sqrt(sum(a * a for a in query_vec))
            nb = math.sqrt(sum(b * b for b in vec))
            score = dot / (na * nb) if na and nb else 0.0
            scored.append((score, row))

        scored.sort(key=lambda p: p[0], reverse=True)
        return [row for _score, row in scored[:k]]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying database connection."""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rows_to_dicts(cursor: sqlite3.Cursor) -> List[dict]:
        """Convert raw cursor rows into a list of dicts keyed by column name."""
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
