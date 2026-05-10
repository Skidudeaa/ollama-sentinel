"""AST-based Python import-graph resolver.

Promoted to ``ollama_sentinel.context`` from ``research_agent.tools`` so
the sentinel can use it without forcing the ``[research]`` extras
(langchain, playwright, llama-index) onto users who only want file
review. Used by the structural-recall path in ``FileProcessor`` and the
upcoming pytest plugin's suspect_commits ranking.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("ollama-sentinel.import-resolver")


class ImportResolver:
    """Resolve Python imports to file paths within a repository.

    Parses ``ast.Import`` and ``ast.ImportFrom`` nodes to build an
    import dependency graph over all Python files under *repo_path*.
    External packages (those that do not map to a file inside the repo)
    are silently skipped.
    """

    def __init__(self, repo_path: str, language: str = "python") -> None:
        self.repo_path = Path(repo_path).resolve()
        self.language = language
        self._import_cache: Optional[Dict[str, List[str]]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_imports(self, file_path: str) -> List[str]:
        """Return resolved absolute file paths imported by *file_path*.

        Handles ``import module``, ``from module import name``, and
        relative imports (``from .x import y``).  Files with syntax
        errors return an empty list and emit a warning.
        """
        source_path = Path(file_path).resolve()
        try:
            source_text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Cannot read %s: %s", source_path, exc)
            return []

        try:
            tree = ast.parse(source_text, filename=str(source_path))
        except SyntaxError as exc:
            logger.warning("Syntax error in %s: %s", source_path, exc)
            return []

        package_dir = str(source_path.parent)
        resolved: List[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    path = self._resolve_module_to_path(alias.name)
                    if path is not None:
                        resolved.append(path)

            elif isinstance(node, ast.ImportFrom):
                level = node.level or 0

                if level > 0:
                    # Relative import
                    if node.module:
                        path = self._resolve_module_to_path(
                            node.module, package_dir=package_dir, level=level
                        )
                        if path is not None:
                            resolved.append(path)
                    else:
                        # ``from . import name`` – each name may be a sub-module
                        for alias in node.names:
                            path = self._resolve_module_to_path(
                                alias.name, package_dir=package_dir, level=level
                            )
                            if path is not None:
                                resolved.append(path)
                else:
                    # Absolute import
                    if node.module:
                        path = self._resolve_module_to_path(node.module)
                        if path is not None:
                            resolved.append(path)

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: List[str] = []
        for p in resolved:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return unique

    def resolve_dependents(self, file_path: str) -> List[str]:
        """Return files in the repo that import *file_path*."""
        target = str(Path(file_path).resolve())
        graph = self._scan_all_imports()
        dependents: List[str] = []
        for source, imports in graph.items():
            if target in imports:
                dependents.append(source)
        return dependents

    def build_graph(
        self, entry_files: Optional[List[str]] = None
    ) -> Dict[str, Dict[str, List[str]]]:
        """Build a bidirectional import graph.

        Returns ``{file: {"imports": [...], "imported_by": [...]}}``
        for every Python file discovered (or only *entry_files* if
        provided).
        """
        if entry_files is not None:
            files = [str(Path(f).resolve()) for f in entry_files]
        else:
            files = [
                str(p.resolve())
                for p in self.repo_path.rglob("*.py")
                if p.is_file()
            ]

        graph: Dict[str, Dict[str, List[str]]] = {}

        # Populate "imports" edge for each file
        for fpath in files:
            imports = self.resolve_imports(fpath)
            graph.setdefault(fpath, {"imports": [], "imported_by": []})
            graph[fpath]["imports"] = imports

            # Ensure every imported file has an entry
            for imp in imports:
                graph.setdefault(imp, {"imports": [], "imported_by": []})

        # Populate "imported_by" (reverse edges)
        for fpath, info in graph.items():
            for imp in info["imports"]:
                if imp in graph:
                    if fpath not in graph[imp]["imported_by"]:
                        graph[imp]["imported_by"].append(fpath)

        return graph

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_all_imports(self) -> Dict[str, List[str]]:
        """Cached scan of every Python file's imports in the repo."""
        if self._import_cache is not None:
            return self._import_cache

        cache: Dict[str, List[str]] = {}
        for py_file in self.repo_path.rglob("*.py"):
            if not py_file.is_file():
                continue
            fpath = str(py_file.resolve())
            cache[fpath] = self.resolve_imports(fpath)

        self._import_cache = cache
        return self._import_cache

    def _resolve_module_to_path(
        self,
        module_name: str,
        package_dir: Optional[str] = None,
        level: int = 0,
    ) -> Optional[str]:
        """Convert a module name to an actual file path inside the repo.

        For relative imports (*level* > 0), *package_dir* is the
        directory of the importing file.  We go up ``level - 1``
        directories from *package_dir* to find the anchor, then resolve
        *module_name* from there.

        Returns ``None`` when the module cannot be found inside the repo.
        """
        if level > 0 and package_dir is not None:
            # Relative import: anchor from the importing file's directory
            anchor = Path(package_dir).resolve()
            for _ in range(level - 1):
                anchor = anchor.parent
            parts = module_name.split(".")
            candidate_base = anchor.joinpath(*parts)
        else:
            # Absolute import
            parts = module_name.split(".")
            candidate_base = self.repo_path.joinpath(*parts)

        # Try module.py first, then module/__init__.py
        as_file = candidate_base.with_suffix(".py")
        if as_file.is_file() and self._is_inside_repo(as_file):
            return str(as_file.resolve())

        as_package = candidate_base / "__init__.py"
        if as_package.is_file() and self._is_inside_repo(as_package):
            return str(as_package.resolve())

        return None

    def _is_inside_repo(self, path: Path) -> bool:
        """Check that *path* is inside the repository root."""
        try:
            path.resolve().relative_to(self.repo_path)
            return True
        except ValueError:
            return False
