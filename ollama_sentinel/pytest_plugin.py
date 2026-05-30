"""pytest plugin: link objective test failures to open Findings.

When a test fails, its crash location is matched against the open Findings
in the project's ViolationDB. A match promotes the model's *opinion* (a
Finding) into a corroborated *event* (an Incident) carrying the test node
id as proof. This is the bridge that turns "the model keeps saying X" into
"X was confirmed by a real failure at this line, on this commit."

The plugin is opt-in and zero-cost when inactive: with no
``ollama-sentinel.yaml`` and no ViolationDB in the working tree it does
nothing, has no import-time side effects, and adds no per-test overhead.
"""

from __future__ import annotations

import logging
import pathlib
from typing import List, Optional, Tuple

log = logging.getLogger("ollama_sentinel.pytest_plugin")


def _extract_failure_location(report) -> Optional[Tuple[str, int]]:
    """Return the ``(path, line)`` where a failed test actually crashed.

    Reads ``report.longrepr.reprcrash`` — the last traceback frame, which
    points at the failing assertion rather than the test definition. Returns
    ``None`` for reports without structured crash info (collection errors,
    string longreprs, skips), so the caller can ignore them.
    """
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    if crash is None:
        return None
    path = getattr(crash, "path", None)
    lineno = getattr(crash, "lineno", None)
    if path is None or lineno is None:
        return None
    return (str(path), int(lineno))


def _match_findings(
    findings: List[dict], *, failure_line: int, tolerance: int
) -> List[dict]:
    """Return findings whose line range overlaps ``failure_line`` ± tolerance.

    A finding spanning ``[line_start, line_end]`` matches when the failure
    line falls inside the range widened by ``tolerance`` on each side (A6).
    """
    matched = []
    for f in findings:
        lo = f["line_start"] - tolerance
        hi = f["line_end"] + tolerance
        if lo <= failure_line <= hi:
            matched.append(f)
    return matched


def _rank_suspect_commits(
    *,
    failing_file: str,
    neighbor_files: List[str],
    recent_commits: List[Tuple[str, List[str]]],
    limit: int,
) -> List[str]:
    """Rank commits suspected of causing a failure (A5).

    ``recent_commits`` is ``[(sha, files_touched), ...]`` newest-first. A
    commit is a suspect when it touched the failing file or one of its 1-hop
    import neighbors. Suspects are returned in the input (recency) order and
    truncated to ``limit``. This is the deliberately simple heuristic the
    plan prescribes for v0.2 — full import-graph blame traversal is v0.2.1.
    """
    relevant = {failing_file, *neighbor_files}
    suspects = [
        sha
        for sha, touched in recent_commits
        if relevant.intersection(touched)
    ]
    return suspects[:limit]


# --------------------------------------------------------------------------- #
# pytest hooks — opt-in, zero-cost when inactive.
# --------------------------------------------------------------------------- #

_SUSPECT_COMMIT_LIMIT = 5


def pytest_addoption(parser) -> None:
    """Register the opt-in ini options.

    The plugin loads via the ``pytest11`` entry point for every pytest run,
    so it must default to *off*: nothing happens unless ``ollama_sentinel``
    is explicitly set true in the project's pytest config.
    """
    parser.addini(
        "ollama_sentinel",
        help="Link test failures to ollama-sentinel Findings as Incidents.",
        type="bool",
        default=False,
    )
    parser.addini(
        "ollama_sentinel_config",
        help="Path to ollama-sentinel.yaml (relative to rootdir).",
        default="ollama-sentinel.yaml",
    )
    parser.addini(
        "ollama_sentinel_tolerance",
        help="Line tolerance for matching a failure to a Finding span.",
        default="5",
    )


def pytest_configure(config) -> None:
    """Activate the linker only when opted in, configured, and backed by a DB.

    Three gates, cheapest first: the ini switch, the config file's existence,
    then the ViolationDB's. Failing any gate leaves the run completely
    untouched — no plugin object registered, no hooks fired.
    """
    if not config.getini("ollama_sentinel"):
        return

    config_path = pathlib.Path(config.getini("ollama_sentinel_config"))
    if not config_path.is_absolute():
        config_path = pathlib.Path(config.rootpath) / config_path
    if not config_path.exists():
        return

    try:
        tolerance = int(config.getini("ollama_sentinel_tolerance"))
    except (TypeError, ValueError):
        tolerance = 5

    linker = _IncidentLinker(config_path, tolerance=tolerance)
    if not linker.active:
        linker.close()
        return

    config.pluginmanager.register(linker, "ollama-sentinel-incident-linker")
    config.add_cleanup(linker.close)


class _IncidentLinker:
    """Collects failed-test crash locations and links them to open Findings.

    Built only when all activation gates pass, so its mere existence means
    the DB is open. Holds the connection across the session and closes it in
    ``pytest_sessionfinish`` (with ``config.add_cleanup`` as a backstop).
    """

    def __init__(self, config_path: pathlib.Path, *, tolerance: int) -> None:
        self._tolerance = tolerance
        self._failures: List[Tuple[str, str, int]] = []  # (nodeid, abspath, line)
        self.db = None
        self._repo_root: Optional[pathlib.Path] = None

        # Heavy imports deferred to keep import-time side effects at zero.
        from .config import load_config
        from .violation_db import ViolationDB

        config = load_config(config_path)
        if config is None:
            return
        repo_root = pathlib.Path(config.watch.directory).resolve()
        db_path = repo_root / config.memory.db_path
        if not db_path.exists():
            return
        self._repo_root = repo_root
        self.db = ViolationDB(str(db_path))

    @property
    def active(self) -> bool:
        return self.db is not None

    def pytest_runtest_logreport(self, report) -> None:
        """Record the crash location of each failed test's call phase."""
        if report.when != "call" or not report.failed:
            return
        location = _extract_failure_location(report)
        if location is None:
            return
        path, line = location
        self._failures.append((report.nodeid, path, line))

    def pytest_sessionfinish(self, session) -> None:
        """Turn collected failures into Incidents. Best-effort; never raises."""
        if self.db is None:
            return
        try:
            self._link_failures()
        except Exception:  # pragma: no cover - defensive: must not fail the run
            log.debug("incident linking failed", exc_info=True)
        finally:
            self.close()

    def _link_failures(self) -> None:
        open_findings = self.db.get_all_unresolved()
        for nodeid, abs_path, line in self._failures:
            rel = self._relativize(abs_path)
            if rel is None:
                continue
            candidates = [f for f in open_findings if f["file_path"] == rel]
            matched = _match_findings(
                candidates, failure_line=line, tolerance=self._tolerance
            )
            if not matched:
                log.debug("no matching Finding for %s:%s", rel, line)
                continue
            triggering, suspects = self._git_context(rel)
            for finding in matched:
                self.db.persist_incident(
                    _make_incident(
                        finding_id=finding["id"],
                        nodeid=nodeid,
                        symptom_file=rel,
                        symptom_line=line,
                        triggering=triggering,
                        suspects=suspects,
                    )
                )

    def _relativize(self, abs_path: str) -> Optional[str]:
        """Express a crash path relative to the watched repo root.

        Findings store paths relative to ``watch.directory``; pytest reports
        absolute crash paths. A failure outside the repo (third-party code)
        relativizes to ``None`` and is ignored.
        """
        if self._repo_root is None:
            return None
        try:
            return str(pathlib.Path(abs_path).resolve().relative_to(self._repo_root))
        except ValueError:
            return None

    def _git_context(
        self, rel_file: str
    ) -> Tuple[Optional[str], Optional[List[str]]]:
        """Best-effort (HEAD sha, suspect commits) for the failing file.

        Returns ``(None, None)`` when the project isn't a git repo or anything
        goes wrong — the simple v0.2 heuristic from the plan: recent commits
        touching the failing file or its 1-hop import neighbours. Full
        import-graph blame traversal is deferred to v0.2.1.
        """
        if self._repo_root is None:
            return None, None
        try:
            import git

            repo = git.Repo(self._repo_root)
            head_sha = repo.head.commit.hexsha
            neighbors = self._import_neighbors(rel_file)
            # Cost note: this scans 20 commits' diff-stats per matching failure.
            # Fine at v0.2 scale; if it bites, memoize the scan once per session.
            recent: List[Tuple[str, List[str]]] = []
            for commit in repo.iter_commits(max_count=20):
                touched = list(commit.stats.files.keys())
                recent.append((commit.hexsha, touched))
            suspects = _rank_suspect_commits(
                failing_file=rel_file,
                neighbor_files=neighbors,
                recent_commits=recent,
                limit=_SUSPECT_COMMIT_LIMIT,
            )
            return head_sha, (suspects or None)
        except Exception:
            log.debug("git context unavailable for %s", rel_file, exc_info=True)
            return None, None

    def _import_neighbors(self, rel_file: str) -> List[str]:
        """1-hop import neighbours of ``rel_file``, relative to the repo root.

        Uses ``ImportResolver`` when available; returns ``[]`` for non-Python
        files or any resolver failure (the heuristic then keys off the failing
        file alone).
        """
        if self._repo_root is None or not rel_file.endswith(".py"):
            return []
        try:
            from .context.import_resolver import ImportResolver

            resolver = ImportResolver(str(self._repo_root))
            abs_file = str(self._repo_root / rel_file)
            neighbors_abs = set(resolver.resolve_imports(abs_file)) | set(
                resolver.resolve_dependents(abs_file)
            )
            out = []
            for path in neighbors_abs:
                try:
                    out.append(str(pathlib.Path(path).resolve().relative_to(self._repo_root)))
                except ValueError:
                    continue
            return out
        except Exception:
            log.debug("import neighbours unavailable for %s", rel_file, exc_info=True)
            return []

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None


def _make_incident(
    *,
    finding_id: int,
    nodeid: str,
    symptom_file: str,
    symptom_line: int,
    triggering: Optional[str],
    suspects: Optional[List[str]],
):
    """Build a ``test_failure`` Incident. Import kept local (zero-cost)."""
    from .violation_db import Incident

    return Incident(
        finding_id=finding_id,
        confirming_signal="test_failure",
        confirming_artifact=nodeid,
        triggering_commit=triggering,
        suspect_commits=suspects,
        symptom_file=symptom_file,
        symptom_line=symptom_line,
    )
