# Surface Findings as SARIF — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serialize open `ViolationDB` findings to a `findings.sarif` artifact — re-anchored to current line numbers by verbatim excerpt — so they appear in the editor Problems panel and GitHub code scanning.

**Architecture:** A new pure module `ollama_sentinel/sarif.py` (relocation + SARIF construction) plus one I/O orchestration function. A `surface` CLI command emits on demand; the watcher regenerates the file best-effort after each review. A required prerequisite makes the finding's `verbatim_excerpt` persistable, which it is not today.

**Tech Stack:** Python 3.10+, SQLite (`ViolationDB`), Typer + Rich (CLI), pytest (`asyncio_mode = "auto"`, `tmp_path`).

**Spec:** `docs/superpowers/specs/2026-06-03-surface-findings-sarif-design.md`

**Branch:** `feat/surface-findings-sarif` (already created; the spec commit is its first commit).

**Stacking:** Four pieces, one PR each, stacked linearly. Piece 2 needs Piece 1's column; Piece 3 needs Piece 2's functions; Piece 4 needs Piece 3's orchestrator.

---

## File Structure

| File | Responsibility | Piece |
|------|----------------|-------|
| `ollama_sentinel/violation_db.py` *(modify)* | Add `verbatim_excerpt` column + migration; write it in `persist_findings` | 1 |
| `ollama_sentinel/sarif.py` *(create)* | Pure: `relocate_finding`, `build_sarif`. I/O: `generate_sarif_file`, `SurfaceSummary` | 2, 3 |
| `ollama_sentinel/cli.py` *(modify)* | `surface` command | 3 |
| `ollama_sentinel/watcher.py` *(modify)* | Best-effort SARIF refresh after persist | 4 |
| `tests/test_violation_db.py` *(modify)* | Excerpt persistence + legacy migration | 1 |
| `tests/test_sarif.py` *(create)* | Relocation, `build_sarif`, orchestration | 2, 3 |
| `tests/test_cli.py` *(modify)* | `surface` happy path + no-DB | 3 |
| `tests/test_watcher.py` *(modify)* | Auto-refresh + failure isolation | 4 |

---

## Piece 1: Persist the verbatim excerpt (PR 1)

The `Finding` dataclass carries `verbatim_excerpt`, but the `findings` table has no such column and `persist_findings` discards it. Relocation needs it. Add the column (fresh + legacy DBs) and write it.

### Task 1.1: Excerpt round-trips through persist + read

**Files:**
- Modify: `ollama_sentinel/violation_db.py` (`_CREATE_TABLE`, `_migrate`, `persist_findings`)
- Test: `tests/test_violation_db.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_violation_db.py` (uses the existing `_make_finding` helper and `tmp_path`):

```python
class TestVerbatimExcerptPersistence:
    """The finding's verbatim_excerpt must survive persist → read."""

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_violation_db.py::TestVerbatimExcerptPersistence -v`
Expected: FAIL — `test_persist_stores_verbatim_excerpt` with `KeyError: 'verbatim_excerpt'` (column absent).

- [ ] **Step 3: Add the column to the fresh-DB schema**

In `ollama_sentinel/violation_db.py`, `_CREATE_TABLE`, add the column after `embed_text`:

```python
            embed_text      TEXT,
            verbatim_excerpt TEXT
```

(Change the prior `embed_text TEXT` line to end with a comma.)

- [ ] **Step 4: Add the legacy-DB migration**

In `_migrate`, after the existing `fix_commit_sha` block and before the final `self._conn.commit()`:

```python
                if "verbatim_excerpt" not in cols:
                    self._conn.execute(
                        "ALTER TABLE findings ADD COLUMN verbatim_excerpt TEXT"
                    )
```

- [ ] **Step 5: Write the excerpt in `persist_findings`**

In the `else:` (insert) branch of `persist_findings`, change the INSERT to include the new column and value:

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_violation_db.py -v`
Expected: PASS (new class + all existing violation_db tests still green).

- [ ] **Step 7: Run the full suite (no regressions)**

Run: `pytest tests/ -q`
Expected: all prior tests pass; 2 new tests added.

- [ ] **Step 8: Commit**

```bash
git add ollama_sentinel/violation_db.py tests/test_violation_db.py
git commit -m "feat(violation_db): persist verbatim_excerpt for finding relocation"
```

---

## Piece 2: SARIF pure core — relocation + document (PR 2)

A new module with two pure functions and a `Relocation` dataclass. No I/O. This is the testable heart of the feature.

### Task 2.1: `relocate_finding` re-anchors by excerpt

**Files:**
- Create: `ollama_sentinel/sarif.py`
- Test: `tests/test_sarif.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sarif.py`:

```python
"""Tests for ollama_sentinel.sarif — relocation and SARIF construction."""
import json

from ollama_sentinel.sarif import Relocation, build_sarif, relocate_finding


def _finding(**over):
    base = dict(
        id=1, file_path="src/app.py", line_start=2, line_end=2,
        category="security", severity="high",
        description="eval on untrusted input",
        verbatim_excerpt="x = eval(data)", occurrence_count=1,
    )
    base.update(over)
    return base


class TestRelocateFinding:
    def test_exact_match_at_stored_line(self):
        content = "def f():\n    x = eval(data)\n    return x\n"
        reloc = relocate_finding(content, _finding(line_start=2, line_end=2))
        assert (reloc.start_line, reloc.end_line) == (2, 2)
        assert reloc.status == "relocated"

    def test_drifted_match_found_at_new_line(self):
        # Two lines inserted above; excerpt now on line 4.
        content = "import os\nimport sys\ndef f():\n    x = eval(data)\n"
        reloc = relocate_finding(content, _finding(line_start=2, line_end=2))
        assert reloc.start_line == 4
        assert reloc.status == "relocated"

    def test_multiple_matches_picks_nearest_to_stored(self):
        content = "x = eval(data)\n\n\nx = eval(data)\n"  # lines 1 and 4
        reloc = relocate_finding(content, _finding(line_start=4, line_end=4))
        assert reloc.start_line == 4

    def test_excerpt_not_found_is_stale(self):
        content = "def f():\n    return safe(data)\n"
        reloc = relocate_finding(content, _finding())
        assert reloc.status == "stale"

    def test_empty_excerpt_falls_back_to_stored_lines(self):
        content = "anything\n"
        reloc = relocate_finding(
            content, _finding(verbatim_excerpt="", line_start=7, line_end=9)
        )
        assert (reloc.start_line, reloc.end_line) == (7, 9)
        assert reloc.status == "stored"

    def test_reindented_excerpt_still_matches(self):
        # Excerpt stored with 4-space indent; file now uses 8 spaces.
        content = "def f():\n        x = eval(data)\n"
        reloc = relocate_finding(content, _finding(line_start=2, line_end=2))
        assert reloc.status == "relocated"
        assert reloc.start_line == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sarif.py::TestRelocateFinding -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ollama_sentinel.sarif'`.

- [ ] **Step 3: Write `relocate_finding` + `Relocation`**

Create `ollama_sentinel/sarif.py` with:

```python
"""SARIF 2.1.0 serialization for surfacing findings in editors and CI.

Pure core (relocation + document construction) plus one I/O orchestration
entry added in a later task. Findings store line numbers as of review time;
those drift as files edit, so we re-anchor each finding by its verbatim
excerpt rather than trusting the stored line range.
"""
import hashlib
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Tuple

SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
    "Schemata/sarif-schema-2.1.0.json"
)
SARIF_VERSION = "2.1.0"
INFORMATION_URI = "https://github.com/Skidudeaa/ollama-sentinel"

_SEVERITY_TO_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
}


@dataclass
class Relocation:
    """Where a finding currently lives, and how confidently we know it."""
    start_line: int
    end_line: int
    status: str  # "relocated" | "stored" | "stale"


def _normalize_lines(text: str) -> List[str]:
    """Split into lines with leading/trailing whitespace stripped per line."""
    return [ln.strip() for ln in text.splitlines()]


def _strip_blank_edges(lines: List[str]) -> List[str]:
    """Drop blank lines from both ends, keeping internal blanks."""
    start, end = 0, len(lines)
    while start < end and not lines[start]:
        start += 1
    while end > start and not lines[end - 1]:
        end -= 1
    return lines[start:end]


def relocate_finding(content: str, finding: dict) -> Relocation:
    """Re-anchor a finding to its current line by its verbatim excerpt.

    Whitespace is normalized per line so reindentation does not cause false
    staleness. Empty excerpt → cannot relocate, fall back to stored lines
    (status "stored"). Excerpt absent from the file → status "stale".
    """
    stored_start = int(finding.get("line_start") or 1)
    stored_end = int(finding.get("line_end") or stored_start)
    excerpt = (finding.get("verbatim_excerpt") or "").strip()
    if not excerpt:
        return Relocation(stored_start, stored_end, "stored")

    file_lines = _normalize_lines(content)
    excerpt_lines = _strip_blank_edges(_normalize_lines(excerpt))
    if not excerpt_lines:
        return Relocation(stored_start, stored_end, "stored")

    n = len(excerpt_lines)
    matches: List[int] = []  # 1-based start line of each contiguous match
    for i in range(len(file_lines) - n + 1):
        if file_lines[i:i + n] == excerpt_lines:
            matches.append(i + 1)
    if not matches:
        return Relocation(stored_start, stored_end, "stale")

    best = min(matches, key=lambda ln: abs(ln - stored_start))
    return Relocation(best, best + n - 1, "relocated")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sarif.py::TestRelocateFinding -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/sarif.py tests/test_sarif.py
git commit -m "feat(sarif): relocate findings by verbatim excerpt"
```

### Task 2.2: `build_sarif` constructs the SARIF 2.1.0 document

**Files:**
- Modify: `ollama_sentinel/sarif.py`
- Test: `tests/test_sarif.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sarif.py`:

```python
class TestBuildSarif:
    def _located(self, **over):
        f = _finding(**over)
        return [(f, relocate_finding("    x = eval(data)\n", f))]

    def test_basic_document_shape(self):
        doc = build_sarif(self._located(), tool_version="0.1.1")
        assert doc["version"] == "2.1.0"
        run = doc["runs"][0]
        assert run["tool"]["driver"]["name"] == "ollama-sentinel"
        assert run["tool"]["driver"]["version"] == "0.1.1"
        assert len(run["results"]) == 1

    def test_severity_maps_to_level(self):
        cases = {"critical": "error", "high": "error",
                 "medium": "warning", "low": "note", "weird": "warning"}
        for severity, level in cases.items():
            doc = build_sarif(self._located(severity=severity), tool_version="x")
            assert doc["runs"][0]["results"][0]["level"] == level

    def test_rules_deduped_by_category(self):
        f1 = _finding(id=1, category="bug")
        f2 = _finding(id=2, category="bug")
        f3 = _finding(id=3, category="security")
        located = [(f, relocate_finding("x = eval(data)\n", f)) for f in (f1, f2, f3)]
        doc = build_sarif(located, tool_version="x")
        rule_ids = {r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]}
        assert rule_ids == {"ollama-sentinel/bug", "ollama-sentinel/security"}

    def test_result_location_and_snippet(self):
        doc = build_sarif(self._located(), tool_version="x")
        loc = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"] == "src/app.py"
        assert loc["region"]["snippet"]["text"] == "x = eval(data)"

    def test_fingerprint_stable_across_line_drift(self):
        # Same finding, different stored lines → identical fingerprint.
        a = _finding(line_start=2)
        b = _finding(line_start=99)
        fa = build_sarif([(a, relocate_finding("x = eval(data)\n", a))],
                         tool_version="x")
        fb = build_sarif([(b, relocate_finding("x = eval(data)\n", b))],
                         tool_version="x")
        fp_a = fa["runs"][0]["results"][0]["partialFingerprints"]["ollamaSentinel/v1"]
        fp_b = fb["runs"][0]["results"][0]["partialFingerprints"]["ollamaSentinel/v1"]
        assert fp_a == fp_b

    def test_corroborated_flag_from_ids(self):
        doc = build_sarif(self._located(id=7), tool_version="x",
                          corroborated_ids=frozenset({7}))
        assert doc["runs"][0]["results"][0]["properties"]["corroborated"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sarif.py::TestBuildSarif -v`
Expected: FAIL — `ImportError: cannot import name 'build_sarif'`.

- [ ] **Step 3: Write `build_sarif`**

Append to `ollama_sentinel/sarif.py`:

```python
def _fingerprint(file_path: str, category: str, excerpt: str,
                 description: str) -> str:
    """Stable across line drift: hash path+category+excerpt (never lines)."""
    basis = excerpt.strip() or description.strip()
    raw = f"{file_path}\x00{category}\x00{basis}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_sarif(
    located_findings: Iterable[Tuple[dict, Relocation]],
    *,
    tool_version: str,
    corroborated_ids: FrozenSet[int] = frozenset(),
) -> dict:
    """Build a SARIF 2.1.0 document from (finding, Relocation) pairs.

    Callers must filter out stale relocations first — every pair here is
    emitted as a result.
    """
    rules_by_id: dict = {}
    results: List[dict] = []
    for finding, reloc in located_findings:
        category = str(finding.get("category") or "general")
        severity = str(finding.get("severity") or "").lower()
        level = _SEVERITY_TO_LEVEL.get(severity, "warning")
        rule_id = f"ollama-sentinel/{category}"
        if rule_id not in rules_by_id:
            rules_by_id[rule_id] = {
                "id": rule_id,
                "name": category,
                "shortDescription": {
                    "text": f"Ollama Sentinel {category} finding"
                },
                "defaultConfiguration": {"level": level},
            }

        file_path = str(finding.get("file_path") or "")
        excerpt = finding.get("verbatim_excerpt") or ""
        region: dict = {"startLine": reloc.start_line, "endLine": reloc.end_line}
        if excerpt.strip():
            region["snippet"] = {"text": excerpt}

        results.append({
            "ruleId": rule_id,
            "level": level,
            "message": {"text": str(finding.get("description") or "")},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": file_path, "uriBaseId": "SRCROOT"},
                    "region": region,
                }
            }],
            "partialFingerprints": {
                "ollamaSentinel/v1": _fingerprint(
                    file_path, category, excerpt,
                    str(finding.get("description") or ""),
                )
            },
            "properties": {
                "severity": severity,
                "occurrence_count": int(finding.get("occurrence_count") or 1),
                "corroborated": finding.get("id") in corroborated_ids,
                "relocation": reloc.status,
            },
        })

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [{
            "tool": {
                "driver": {
                    "name": "ollama-sentinel",
                    "version": tool_version,
                    "informationUri": INFORMATION_URI,
                    "rules": list(rules_by_id.values()),
                }
            },
            "results": results,
        }],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sarif.py -v`
Expected: PASS (relocation + build_sarif classes).

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/sarif.py tests/test_sarif.py
git commit -m "feat(sarif): build SARIF 2.1.0 document from located findings"
```

---

## Piece 3: Orchestration + `surface` command (PR 3)

The one I/O function that reads the DB and files, relocates, writes `findings.sarif`, and the CLI verb that drives it.

### Task 3.1: `generate_sarif_file` orchestration

**Files:**
- Modify: `ollama_sentinel/sarif.py`
- Test: `tests/test_sarif.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sarif.py`:

```python
import pathlib

import pytest

from ollama_sentinel.sarif import generate_sarif_file
from ollama_sentinel.violation_db import Finding, ViolationDB


def _seed(db_path: pathlib.Path, file_path: str, findings):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = ViolationDB(str(db_path))
    db.persist_findings(file_path, findings)
    db.close()


class TestGenerateSarifFile:
    def test_writes_sarif_for_open_findings(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text(
            "def f():\n    x = eval(data)\n    return x\n"
        )
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed(db_path, "src/app.py", [
            Finding("src/app.py", 2, 2, "security", "high",
                    "eval on untrusted input",
                    verbatim_excerpt="x = eval(data)"),
        ])

        db = ViolationDB(str(db_path))
        try:
            summary = generate_sarif_file(
                db, tmp_path, tmp_path / ".ollama_reviews", tool_version="0.1.1",
            )
        finally:
            db.close()

        assert summary.emitted == 1
        assert summary.relocated == 1
        assert summary.stale == 0
        doc = json.loads(summary.path.read_text())
        assert doc["runs"][0]["results"][0]["ruleId"] == "ollama-sentinel/security"

    def test_stale_finding_excluded_and_counted(self, tmp_path):
        (tmp_path / "src").mkdir()
        # File no longer contains the excerpt.
        (tmp_path / "src" / "app.py").write_text("def f():\n    return ok\n")
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed(db_path, "src/app.py", [
            Finding("src/app.py", 2, 2, "security", "high", "gone",
                    verbatim_excerpt="x = eval(data)"),
        ])

        db = ViolationDB(str(db_path))
        try:
            summary = generate_sarif_file(
                db, tmp_path, tmp_path / ".ollama_reviews", tool_version="x",
            )
        finally:
            db.close()

        assert summary.emitted == 0
        assert summary.stale == 1
        doc = json.loads(summary.path.read_text())
        assert doc["runs"][0]["results"] == []

    def test_missing_file_counts_as_stale(self, tmp_path):
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed(db_path, "src/gone.py", [
            Finding("src/gone.py", 1, 1, "bug", "low", "x",
                    verbatim_excerpt="whatever"),
        ])
        db = ViolationDB(str(db_path))
        try:
            summary = generate_sarif_file(
                db, tmp_path, tmp_path / ".ollama_reviews", tool_version="x",
            )
        finally:
            db.close()
        assert summary.stale == 1
        assert summary.emitted == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sarif.py::TestGenerateSarifFile -v`
Expected: FAIL — `ImportError: cannot import name 'generate_sarif_file'`.

- [ ] **Step 3: Write `generate_sarif_file` + `SurfaceSummary`**

Append to `ollama_sentinel/sarif.py`. Add these imports at the top of the file (with the existing imports):

```python
import json
import logging
import pathlib

from .utils import safe_read

log = logging.getLogger("ollama-sentinel")
```

Then append:

```python
@dataclass
class SurfaceSummary:
    """Counts from one SARIF generation pass."""
    emitted: int
    relocated: int
    stale: int
    unverified: int
    path: pathlib.Path


def generate_sarif_file(
    db,
    watch_dir,
    output_dir,
    *,
    tool_version: str,
    out_path=None,
) -> SurfaceSummary:
    """Read open findings, relocate by excerpt, write findings.sarif.

    Read-only with respect to the DB and source: stale findings are excluded
    from the document and counted, never auto-resolved. ``out_path`` overrides
    the default ``output_dir/findings.sarif`` destination.
    """
    watch_dir = pathlib.Path(watch_dir)
    output_dir = pathlib.Path(output_dir)
    rows = db.get_all_unresolved()

    corroborated_ids: set = set()
    paths = sorted({r["file_path"] for r in rows})
    if paths:
        try:
            corroborated_ids = {
                r["id"] for r in db.get_findings_with_incidents(paths)
            }
        except Exception as e:  # corroboration is enrichment; never fatal
            log.warning("Corroboration lookup failed (%s); marking none.", e)

    content_cache: dict = {}

    def _content(rel: str):
        # safe_read returns "" (never raises) for missing/unreadable/symlink/
        # traversal, so check existence explicitly: a gone file is stale, an
        # empty-but-present file goes through relocation like any other.
        if rel not in content_cache:
            abs_path = watch_dir / rel
            content_cache[rel] = (
                safe_read(abs_path, watch_dir) if abs_path.is_file() else None
            )
        return content_cache[rel]

    located: list = []
    relocated = stale = unverified = 0
    for r in rows:
        content = _content(r["file_path"])
        if content is None:
            stale += 1
            continue
        reloc = relocate_finding(content, r)
        if reloc.status == "stale":
            stale += 1
            continue
        if reloc.status == "stored":
            unverified += 1
        else:
            relocated += 1
        located.append((r, reloc))

    doc = build_sarif(
        located, tool_version=tool_version,
        corroborated_ids=frozenset(corroborated_ids),
    )
    sarif_path = pathlib.Path(out_path) if out_path else output_dir / "findings.sarif"
    sarif_path.parent.mkdir(parents=True, exist_ok=True)
    sarif_path.write_text(json.dumps(doc, indent=2))

    return SurfaceSummary(
        emitted=len(located),
        relocated=relocated,
        stale=stale,
        unverified=unverified,
        path=sarif_path,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sarif.py -v`
Expected: PASS (all three classes).

- [ ] **Step 5: Commit**

```bash
git add ollama_sentinel/sarif.py tests/test_sarif.py
git commit -m "feat(sarif): generate_sarif_file orchestration (read-only)"
```

### Task 3.2: `ollama-sentinel surface` CLI command

**Files:**
- Modify: `ollama_sentinel/cli.py` (add command after `incidents`, near line 523)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (reuses `_make_report_config` and `_seed_db`):

```python
class TestSurfaceCommand:
    """Tests for 'ollama-sentinel surface'."""

    def test_surface_writes_sarif(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text(
            "def f():\n    x = eval(data)\n"
        )
        db_path = tmp_path / ".ollama_reviews" / "memory.db"
        _seed_db(db_path, [])  # creates the DB
        db = ViolationDB(str(db_path))
        db.persist_findings("src/app.py", [
            Finding("src/app.py", 2, 2, "security", "high", "eval",
                    verbatim_excerpt="x = eval(data)"),
        ])
        db.close()

        result = runner.invoke(app, ["surface", "--config", str(cfg)])
        assert result.exit_code == 0
        sarif = tmp_path / ".ollama_reviews" / "findings.sarif"
        assert sarif.exists()
        doc = json.loads(sarif.read_text())
        assert doc["version"] == "2.1.0"
        assert "Wrote 1 findings" in result.output

    def test_surface_no_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_report_config(tmp_path)
        result = runner.invoke(app, ["surface", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "No violation database" in result.output
```

Note: `_seed_db(db_path, [])` persists an empty list (a no-op insert that still creates the DB file via `ViolationDB.__init__`). The subsequent explicit `persist_findings` adds the row with an excerpt.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py::TestSurfaceCommand -v`
Expected: FAIL — `surface` is not a registered command (non-zero exit / usage error).

- [ ] **Step 3: Add the `surface` command**

In `ollama_sentinel/cli.py`, after the `incidents` command (ends ~line 522) and before `triage`:

```python
@app.command()
def surface(
    config_path: str = typer.Option(
        "ollama-sentinel.yaml", "--config", "-c",
        help="Path to configuration file",
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o",
        help="SARIF output path (default: <reviews-dir>/findings.sarif)",
    ),
):
    """Emit open findings as SARIF for editor Problems panels and CI.

    Findings are re-anchored to their current line by verbatim excerpt;
    stale findings (excerpt no longer present) are reported but excluded.
    Read-only: never edits source, never changes finding state.
    """
    from . import __version__
    from .sarif import generate_sarif_file
    from .violation_db import ViolationDB

    config = _load_config_or_exit(config_path)
    watch_dir = pathlib.Path(config.watch.directory).resolve()
    db_path = watch_dir / config.memory.db_path
    if not db_path.exists():
        console.print(
            "[yellow]No violation database found. Run some reviews first.[/yellow]"
        )
        raise typer.Exit()

    output_dir = watch_dir / config.output.directory
    out_path = pathlib.Path(output).resolve() if output else None

    db = ViolationDB(str(db_path))
    try:
        summary = generate_sarif_file(
            db, watch_dir, output_dir,
            tool_version=__version__, out_path=out_path,
        )
    finally:
        db.close()

    console.print(
        f"[green]Wrote {summary.emitted} findings → {summary.path}[/green] "
        f"[dim]({summary.relocated} relocated, "
        f"{summary.unverified} unverified, {summary.stale} stale)[/dim]"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py::TestSurfaceCommand -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'surface' command to emit findings.sarif"
```

---

## Piece 4: Watcher auto-refresh (PR 4)

Keep `findings.sarif` fresh while `ollama-sentinel run` is active. Best-effort: a SARIF failure must never break review saving.

### Task 4.1: Refresh SARIF after persist, isolate failures

**Files:**
- Modify: `ollama_sentinel/watcher.py` (`process_change`, after the persist block ~line 269, inside `if self.violation_db:`)
- Test: `tests/test_watcher.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watcher.py`. Add any missing imports at the top (`json`, `pathlib`, `yaml`, `Change`, `FileChange`, `FileSentinel`); the module already imports `watcher as w`.

```python
import json as _json
import pathlib as _pathlib

import yaml as _yaml

from watchfiles import Change
from ollama_sentinel.processor import FileChange
from ollama_sentinel.watcher import FileSentinel


def _watcher_cfg(tmp_path):
    """Config rooted at tmp_path: memory on, embeddings off (no network)."""
    cfg = {
        "watch": {"directory": str(tmp_path), "recursive": True},
        "ollama": {
            "host": "http://localhost:11434",
            "models": {"default": {"name": "m", "system_prompt": "p"}},
            "request_timeout": 30,
        },
        "processing": {"git_diff_mode": False, "grounding": True},
        "output": {"directory": ".ollama_reviews", "console_output": False},
        "memory": {"enabled": True, "db_path": ".ollama_reviews/memory.db",
                   "semantic_recall": False, "structural_recall": False},
        "embedding": {"enabled": False},
    }
    p = tmp_path / "ollama-sentinel.yaml"
    p.write_text(_yaml.dump(cfg, sort_keys=False))
    return p


def _fake_review(*_a, **_k):
    async def _run(file_change, model_role="default"):
        return {
            "summary": "review",
            "findings": [{
                "line_start": 2, "line_end": 2, "category": "security",
                "severity": "high", "verbatim_excerpt": "x = eval(data)",
                "description": "eval on untrusted input",
            }],
        }
    return _run


class TestSarifAutoRefresh:
    async def test_process_change_writes_sarif(self, tmp_path, monkeypatch):
        cfg = _watcher_cfg(tmp_path)
        src = tmp_path / "app.py"
        src.write_text("def f():\n    x = eval(data)\n    return x\n")

        sentinel = FileSentinel(cfg)
        monkeypatch.setattr(sentinel.processor, "generate_review", _fake_review())
        try:
            await sentinel.process_change(
                FileChange(path=src, change_type=Change.modified)
            )
        finally:
            await sentinel.processor.close()

        sarif = tmp_path / ".ollama_reviews" / "findings.sarif"
        assert sarif.exists()
        doc = _json.loads(sarif.read_text())
        assert doc["runs"][0]["results"][0]["ruleId"] == "ollama-sentinel/security"

    async def test_sarif_failure_does_not_break_review(self, tmp_path, monkeypatch):
        cfg = _watcher_cfg(tmp_path)
        src = tmp_path / "app.py"
        src.write_text("def f():\n    x = eval(data)\n    return x\n")

        sentinel = FileSentinel(cfg)
        monkeypatch.setattr(sentinel.processor, "generate_review", _fake_review())

        import ollama_sentinel.sarif as sarif_mod

        def _boom(*_a, **_k):
            raise RuntimeError("sarif blew up")

        monkeypatch.setattr(sarif_mod, "generate_sarif_file", _boom)
        try:
            # Must NOT raise despite the SARIF failure.
            await sentinel.process_change(
                FileChange(path=src, change_type=Change.modified)
            )
        finally:
            await sentinel.processor.close()

        # The review itself still saved.
        assert (tmp_path / ".ollama_reviews" / "app.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_watcher.py::TestSarifAutoRefresh -v`
Expected: FAIL — `test_process_change_writes_sarif` fails because no `findings.sarif` is written (the refresh hook does not exist yet).

- [ ] **Step 3: Add the best-effort refresh hook**

In `ollama_sentinel/watcher.py`, in `process_change`, immediately after the finding-persistence `try/except` block (the one ending with `log.warning(f"Finding persistence failed for {rel_path}: {e}")`) and still inside `if self.violation_db:`:

```python
            # Refresh the SARIF surface so editors/CI see current findings.
            # Best-effort: a SARIF failure must never block review saving.
            try:
                from . import __version__
                from .sarif import generate_sarif_file
                await asyncio.to_thread(
                    generate_sarif_file,
                    self.violation_db,
                    self.processor.watch_dir,
                    self.processor.output_dir,
                    tool_version=__version__,
                )
            except Exception as e:
                log.warning(f"SARIF refresh failed for {rel_path}: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_watcher.py::TestSarifAutoRefresh -v`
Expected: PASS (2 tests). The failure-isolation test passes because the hook swallows the `RuntimeError` and `save_review` still runs.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: all green (≈ 6 new tests beyond Pieces 1–3).

- [ ] **Step 6: Commit**

```bash
git add ollama_sentinel/watcher.py tests/test_watcher.py
git commit -m "feat(watcher): refresh findings.sarif after each review (best-effort)"
```

---

## Final: docs + manual smoke

### Task 5.1: Document the command

**Files:**
- Modify: `ollama_sentinel/CLAUDE.md` (Build & Run command list; module table for `sarif.py`)
- Modify: `README.md` if it lists commands (check first: `grep -n "ollama-sentinel " README.md`)

- [ ] **Step 1: Add to the CLAUDE.md command list**

Under "Build & Run", add after the `incidents` line:

```
ollama-sentinel surface             # emit open findings to .ollama_reviews/findings.sarif
```

- [ ] **Step 2: Add `sarif.py` to the module table**

In the `### Key modules` table:

```
| `ollama_sentinel/sarif.py` | SARIF 2.1.0 surface: excerpt-based relocation, document build, `generate_sarif_file` (read-only) + the `surface` command |
```

- [ ] **Step 3: Commit**

```bash
git add ollama_sentinel/CLAUDE.md README.md
git commit -m "docs: document the surface command and sarif module"
```

### Task 5.2: Manual smoke (run once, not automated)

- [ ] Against this repo: `ollama-sentinel surface` (after some reviews exist) writes `.ollama_reviews/findings.sarif`; open it in Cursor with the SARIF Viewer extension and confirm findings land in the Problems panel with click-to-jump.
- [ ] Confirm `python -c "import json,sys; json.load(open('.ollama_reviews/findings.sarif'))"` parses.

---

## Self-Review

**Spec coverage:**
- `relocate_finding` (excerpt re-anchoring, drift, stale, empty) → Task 2.1 ✓
- `build_sarif` (level map, rule dedup, fingerprints, snippet, corroborated, properties) → Task 2.2 ✓
- `generate_sarif_file` (read DB, safe_read, exclude+count stale, write) → Task 3.1 ✓
- `surface` CLI (config resolve, no-DB message, summary, `-o`) → Task 3.2 ✓
- Watcher auto-refresh (best-effort, `asyncio.to_thread`, gated on `violation_db`) → Task 4.1 ✓
- Severity→level mapping (5 cases) → Task 2.2 ✓
- `partialFingerprints` stable across drift → Task 2.2 ✓
- `corroborated` via `get_findings_with_incidents` → Tasks 2.2 + 3.1 ✓
- **Required prerequisite — persist `verbatim_excerpt`** → Piece 1 ✓
- Stale reported, never auto-resolved (read-only) → Task 3.1 (no `mark_resolved` call) ✓
- Tests: relocation / build / orchestration / CLI / watcher → all pieces ✓
- Docs → Task 5.1 ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every command has expected output. ✓

**Type consistency:** `Relocation(start_line, end_line, status)`, `SurfaceSummary(emitted, relocated, stale, unverified, path)`, `generate_sarif_file(db, watch_dir, output_dir, *, tool_version, out_path=None)`, `build_sarif(located_findings, *, tool_version, corroborated_ids)` — names identical across the tasks that define and call them. `ruleId` namespace `ollama-sentinel/<category>` and fingerprint key `ollamaSentinel/v1` consistent between Task 2.2 and its tests. ✓

**Note for the implementer:** `Finding` is positional `(file_path, line_start, line_end, category, severity, description, verbatim_excerpt="")` — the test seeds pass `verbatim_excerpt=` as a keyword, which matches the dataclass field order. Confirm before running Piece 3 tests.
