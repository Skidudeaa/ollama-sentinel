"""Tests for research_agent.tools.import_resolver."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from research_agent.tools.import_resolver import ImportResolver


# ---------------------------------------------------------------------------
# Helpers – build a tiny synthetic project inside tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Create a synthetic multi-file Python project.

    Layout::

        project/
            __init__.py
            main.py        (from project.utils import helper; from project.models import User;
                            from project.sub.helpers import run)
            utils.py       (import os)
            models.py      (from project.utils import helper)
            sub/
                __init__.py
                worker.py  (from ..models import User; from .helpers import run)
                helpers.py (no local imports)
    """
    pkg = tmp_path / "project"
    sub = pkg / "sub"
    sub.mkdir(parents=True)

    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text(textwrap.dedent("""\
        from project.utils import helper
        from project.models import User
        from project.sub.helpers import run
    """))
    (pkg / "utils.py").write_text(textwrap.dedent("""\
        import os
    """))
    (pkg / "models.py").write_text(textwrap.dedent("""\
        from project.utils import helper
    """))
    (sub / "__init__.py").write_text("")
    (sub / "worker.py").write_text(textwrap.dedent("""\
        from ..models import User
        from .helpers import run
    """))
    (sub / "helpers.py").write_text(textwrap.dedent("""\
        # no local imports
        pass
    """))

    return tmp_path          # repo root is the *parent* of "project"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolveImports:
    """resolve_imports returns local file paths for in-repo imports."""

    def test_resolves_multiple_imports(self, project: Path) -> None:
        resolver = ImportResolver(str(project))
        main_py = str(project / "project" / "main.py")

        result = resolver.resolve_imports(main_py)

        assert str(project / "project" / "utils.py") in result
        assert str(project / "project" / "models.py") in result
        assert str(project / "project" / "sub" / "helpers.py") in result
        assert len(result) == 3

    def test_relative_import_resolved(self, project: Path) -> None:
        resolver = ImportResolver(str(project))
        worker_py = str(project / "project" / "sub" / "worker.py")

        result = resolver.resolve_imports(worker_py)

        assert str(project / "project" / "models.py") in result
        assert str(project / "project" / "sub" / "helpers.py") in result

    def test_external_package_skipped(self, project: Path) -> None:
        resolver = ImportResolver(str(project))
        utils_py = str(project / "project" / "utils.py")

        result = resolver.resolve_imports(utils_py)

        # ``import os`` should not resolve to any file inside the repo
        assert result == []

    def test_syntax_error_returns_empty(self, project: Path, caplog) -> None:
        bad_file = project / "bad.py"
        bad_file.write_text("def foo(:\n")

        resolver = ImportResolver(str(project))
        result = resolver.resolve_imports(str(bad_file))

        assert result == []
        assert any("Syntax error" in rec.message for rec in caplog.records)


class TestResolveDependents:
    """resolve_dependents finds all files that import a given file."""

    def test_dependents_of_utils(self, project: Path) -> None:
        resolver = ImportResolver(str(project))
        utils_py = str(project / "project" / "utils.py")

        dependents = resolver.resolve_dependents(utils_py)

        # main.py and models.py both import utils
        assert str(project / "project" / "main.py") in dependents
        assert str(project / "project" / "models.py") in dependents

    def test_dependents_of_helpers(self, project: Path) -> None:
        resolver = ImportResolver(str(project))
        helpers_py = str(project / "project" / "sub" / "helpers.py")

        dependents = resolver.resolve_dependents(helpers_py)

        assert str(project / "project" / "sub" / "worker.py") in dependents


class TestBuildGraph:
    """build_graph produces a correct bidirectional mapping."""

    def test_graph_structure(self, project: Path) -> None:
        resolver = ImportResolver(str(project))
        graph = resolver.build_graph()

        utils_py = str(project / "project" / "utils.py")
        main_py = str(project / "project" / "main.py")
        models_py = str(project / "project" / "models.py")

        # utils.py should list main.py and models.py as imported_by
        assert main_py in graph[utils_py]["imported_by"]
        assert models_py in graph[utils_py]["imported_by"]

        # main.py should list utils.py and models.py as imports
        assert utils_py in graph[main_py]["imports"]
        assert models_py in graph[main_py]["imports"]

    def test_graph_with_entry_files(self, project: Path) -> None:
        resolver = ImportResolver(str(project))
        main_py = str(project / "project" / "main.py")

        graph = resolver.build_graph(entry_files=[main_py])

        assert main_py in graph
        assert len(graph[main_py]["imports"]) == 3

    def test_circular_imports_no_crash(self, tmp_path: Path) -> None:
        """Circular imports are recorded in both directions."""
        pkg = tmp_path / "circ"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "a.py").write_text("from circ.b import thing\n")
        (pkg / "b.py").write_text("from circ.a import other\n")

        resolver = ImportResolver(str(tmp_path))
        graph = resolver.build_graph()

        a_py = str(tmp_path / "circ" / "a.py")
        b_py = str(tmp_path / "circ" / "b.py")

        assert b_py in graph[a_py]["imports"]
        assert a_py in graph[b_py]["imports"]
        assert a_py in graph[b_py]["imported_by"]
        assert b_py in graph[a_py]["imported_by"]


class TestFromDotImport:
    """``from . import name`` where module is None."""

    def test_from_dot_import(self, tmp_path: Path) -> None:
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "utils.py").write_text("x = 1\n")
        (pkg / "main.py").write_text("from . import utils\n")

        resolver = ImportResolver(str(tmp_path))
        result = resolver.resolve_imports(str(pkg / "main.py"))

        assert str(pkg / "utils.py") in result
