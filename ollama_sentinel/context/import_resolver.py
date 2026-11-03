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
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

logger = logging.getLogger("ollama-sentinel.import-resolver")

_JS_TS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_SUPPORTED_SUFFIXES = (".py", *_JS_TS_SUFFIXES, ".swift")
_IGNORED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".build",
    ".next",
    ".ollama_reviews",
    ".worktrees",
}

_C_STYLE_COMMENT_RE = re.compile(
    r"//[^\n]*|/\*[\s\S]*?\*/",
)
_JS_IMPORT_RE = re.compile(
    r"""(?:
        \b(?:import|export)\b[\s\S]*?\bfrom\s*['"]([^'"]+)['"]
      | \bimport\s*['"]([^'"]+)['"]
      | \b(?:import|require)\s*\(\s*['"]([^'"]+)['"]\s*\)
    )""",
    re.VERBOSE,
)
_SWIFT_DECL_RE = re.compile(
    r"\b(?:class|struct|enum|protocol|actor)\s+([A-Z][A-Za-z0-9_]*)\b"
)
_SWIFT_IDENT_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")


def _strip_c_comments(text: str) -> str:
    return _C_STYLE_COMMENT_RE.sub(" ", text)


def _top_level_swift_decls(text: str) -> List[str]:
    """Names declared on unindented lines of comment-stripped Swift *text*.

    Nested declarations (``enum CodingKeys`` inside every Codable model) are
    indented and excluded: they are not referenceable by bare name from other
    files, and indexing them globally links every file sharing a generic
    nested name. Indentation is used instead of brace depth because braces
    inside string literals (``hasPrefix("{")``) unbalance a depth count and
    would drop every later top-level type in the file.
    """
    names: List[str] = []
    for m in _SWIFT_DECL_RE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        if line_start < len(text) and text[line_start] not in " \t":
            names.append(m.group(1))
    return names


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
        self._swift_type_map: Optional[Dict[str, Set[str]]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def invalidate(self, file_path: Optional[str] = None) -> None:
        """Invalidate cached import graph entries.

        When *file_path* is ``None``, clears the entire import cache and Swift
        type map. When *file_path* is provided, refreshes that file's entry in
        ``_import_cache`` (or removes it if the file was deleted), and resets
        the Swift nominal type map when a ``.swift`` file changes so newly
        declared or renamed types are picked up by ``resolve_dependents``.
        """
        if file_path is None:
            self._import_cache = None
            self._swift_type_map = None
            return

        target = Path(file_path).resolve()
        target_str = str(target)
        if target.suffix.lower() == ".swift":
            self._swift_type_map = None
            self._import_cache = None
            return

        if self._import_cache is not None:
            if target.is_file():
                if target_str not in self._import_cache:
                    # A newly created file may satisfy imports in existing files
                    # that previously resolved to None; force a fresh scan.
                    self._import_cache = None
                else:
                    self._import_cache[target_str] = self.resolve_imports(target_str)
            else:
                self._import_cache.pop(target_str, None)
                for src, imps in list(self._import_cache.items()):
                    if target_str in imps:
                        self._import_cache[src] = [
                            p for p in imps if p != target_str
                        ]

    def resolve_imports(self, file_path: str) -> List[str]:
        """Return resolved absolute file paths imported/referenced by *file_path*.

        Supports Python (``.py`` via ``ast``), TypeScript/JavaScript
        (``.ts``/``.tsx``/``.js``/``.jsx``/``.mjs``/``.cjs``), and Swift
        (``.swift`` intra-module nominal type references). Files with syntax or
        read errors return an empty list and emit a warning.
        """
        source_path = Path(file_path).resolve()
        try:
            source_text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Cannot read %s: %s", source_path, exc)
            return []

        suffix = source_path.suffix.lower()
        if suffix in _JS_TS_SUFFIXES:
            return self._resolve_js_ts_imports(source_path, source_text)
        if suffix == ".swift":
            return self._resolve_swift_references(source_path, source_text)

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
            files = [str(p) for p in self._iter_repo_files(_SUPPORTED_SUFFIXES)]

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

    def _iter_repo_files(self, suffixes: Iterable[str]) -> List[Path]:
        """Yield resolved files under ``repo_path`` matching *suffixes*, skipping ignored dirs."""
        suffix_set = {s.lower() for s in suffixes}
        results: List[Path] = []
        for root, dirs, filenames in os.walk(self.repo_path):
            dirs[:] = [
                d for d in dirs
                if d not in _IGNORED_DIRS and not d.startswith(".git")
            ]
            root_path = Path(root)
            for fname in filenames:
                p = root_path / fname
                if p.suffix.lower() in suffix_set and p.is_file():
                    results.append(p.resolve())
        return results

    def _scan_all_imports(self) -> Dict[str, List[str]]:
        """Cached scan of every supported source file's imports in the repo."""
        if self._import_cache is not None:
            return self._import_cache

        cache: Dict[str, List[str]] = {}
        for src_file in self._iter_repo_files(_SUPPORTED_SUFFIXES):
            fpath = str(src_file)
            cache[fpath] = self.resolve_imports(fpath)

        self._import_cache = cache
        return self._import_cache

    def _resolve_js_ts_imports(
        self, source_path: Path, source_text: str
    ) -> List[str]:
        """Resolve TypeScript/JavaScript relative and ``@/`` imports to repo files."""
        cleaned = _strip_c_comments(source_text)
        resolved: List[str] = []
        seen: Set[str] = set()
        for match in _JS_IMPORT_RE.finditer(cleaned):
            spec = match.group(1) or match.group(2) or match.group(3)
            if not spec:
                continue
            target = self._resolve_js_ts_specifier(source_path.parent, spec)
            if target is not None and target not in seen:
                seen.add(target)
                resolved.append(target)
        return resolved

    def _resolve_js_ts_specifier(
        self, base_dir: Path, spec: str
    ) -> Optional[str]:
        """Resolve a single JS/TS import specifier to a file inside ``repo_path``."""
        candidates_roots: List[Path] = []
        if spec.startswith("."):
            candidates_roots.append((base_dir / spec).resolve())
        elif spec.startswith("@/"):
            rel = spec[2:]
            candidates_roots.append((self.repo_path / "src" / rel).resolve())
            candidates_roots.append((self.repo_path / rel).resolve())
        else:
            return None

        for candidate in candidates_roots:
            # 1. Exact file match (or TS ESM `.js` -> `.ts`/`.tsx`)
            if candidate.is_file() and self._is_inside_repo(candidate):
                return str(candidate)
            if candidate.suffix in (".js", ".jsx", ".mjs"):
                stem_base = candidate.with_suffix("")
                for ts_ext in (".ts", ".tsx"):
                    ts_cand = stem_base.with_suffix(ts_ext)
                    if ts_cand.is_file() and self._is_inside_repo(ts_cand):
                        return str(ts_cand.resolve())
            # 2. Extensionless file (`./foo` -> `./foo.ts`, `./foo.tsx`, etc.)
            for ext in _JS_TS_SUFFIXES:
                with_ext = Path(f"{candidate}{ext}")
                if with_ext.is_file() and self._is_inside_repo(with_ext):
                    return str(with_ext.resolve())
            # 3. Directory index (`./foo` -> `./foo/index.ts`, etc.)
            if candidate.is_dir():
                for ext in _JS_TS_SUFFIXES:
                    idx = candidate / f"index{ext}"
                    if idx.is_file() and self._is_inside_repo(idx):
                        return str(idx.resolve())
        return None

    def _build_swift_type_map(self) -> Dict[str, Set[str]]:
        """Map top-level Swift type names to the repo `.swift` files declaring them."""
        if self._swift_type_map is not None:
            return self._swift_type_map
        type_map: Dict[str, Set[str]] = {}
        for swift_file in self._iter_repo_files((".swift",)):
            try:
                text = _strip_c_comments(swift_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            fpath = str(swift_file)
            for name in _top_level_swift_decls(text):
                type_map.setdefault(name, set()).add(fpath)
        self._swift_type_map = type_map
        return type_map

    def _resolve_swift_references(
        self, source_path: Path, source_text: str
    ) -> List[str]:
        """Resolve intra-repo Swift nominal type references to defining `.swift` files."""
        type_map = self._build_swift_type_map()
        if not type_map:
            return []
        self_str = str(source_path)
        cleaned = _strip_c_comments(source_text)
        seen: Set[str] = set()
        resolved: List[str] = []
        for m in _SWIFT_IDENT_RE.finditer(cleaned):
            ident = m.group(1)
            for target in sorted(type_map.get(ident, ())):
                if target != self_str and target not in seen:
                    seen.add(target)
                    resolved.append(target)
        return resolved

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


# ---------------------------------------------------------------------------
# Symbol-resolution helper for SZZ pickaxe overlay
# ---------------------------------------------------------------------------


def enclosing_symbol(source: str, line: int) -> Optional[str]:
    """Return the qualified name of the innermost function/class/method
    that encloses *line* in *source*, or ``None`` if the line is not
    inside any def or class (e.g., module-level code).

    Used by the v0.2 pytest plugin's pickaxe overlay: given a failing
    test's ``(file, line)``, extract the enclosing symbol so
    ``git log -S <symbol>`` can find every commit that added or removed
    that symbol — directly addressing the ~25% of bug-inducing commits
    that pure file:line blame misses.

    Names are dotted to preserve nesting (e.g., ``Outer.Inner.method``
    or ``outer.<closure>``). Returns the *innermost* containing scope,
    not the file's top-level def. Lines inside both the body and the
    decorator list count as "inside" the def.

    Files with syntax errors return ``None``.
    """
    if line < 1:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    return _walk_for_enclosing(tree, line, prefix="")


def _walk_for_enclosing(
    node: ast.AST, line: int, *, prefix: str
) -> Optional[str]:
    """Recursive helper for enclosing_symbol.

    Walks the AST top-down. At each FunctionDef / AsyncFunctionDef /
    ClassDef whose line range covers *line*, descends to find a more
    deeply nested match before returning the current node's name.
    """
    best: Optional[str] = None
    for child in ast.iter_child_nodes(node):
        if not isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            # Even non-def nodes may contain nested defs (e.g., a try block).
            inner = _walk_for_enclosing(child, line, prefix=prefix)
            if inner is not None:
                best = inner
            continue

        start = _node_start(child)
        end = getattr(child, "end_lineno", None) or start
        if not (start <= line <= end):
            continue

        qualified = f"{prefix}{child.name}" if not prefix else f"{prefix}.{child.name}"
        # Descend looking for a more specific (nested) match.
        nested = _walk_for_enclosing(child, line, prefix=qualified)
        best = nested if nested is not None else qualified
    return best


def _node_start(node: ast.AST) -> int:
    """First line covered by *node*, including any decorators."""
    decorators = getattr(node, "decorator_list", None) or []
    if decorators:
        return min(
            getattr(d, "lineno", getattr(node, "lineno", 1)) for d in decorators
        )
    return getattr(node, "lineno", 1)
