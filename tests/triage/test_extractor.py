"""Tests for ollama_sentinel.triage.extractor."""
import pathlib

from ollama_sentinel.triage.extractor import extract_references


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestExtractorPatterns:
    def test_traceback(self, tmp_path):
        (tmp_path / "ollama_sentinel").mkdir()
        (tmp_path / "ollama_sentinel" / "processor.py").write_text("x = 1\n")
        (tmp_path / "ollama_sentinel" / "utils.py").write_text("x = 1\n")

        refs = extract_references(_load("traceback.log"), cwd=tmp_path)
        paths = sorted({r.path for r in refs})
        assert paths == ["ollama_sentinel/processor.py", "ollama_sentinel/utils.py"]
        lines = sorted(r.line for r in refs)
        assert lines == [17, 42]
        assert all(r.tool_hint == "traceback" for r in refs)

    def test_pytest(self, tmp_path):
        (tmp_path / "tests" / "context").mkdir(parents=True)
        (tmp_path / "tests" / "context" / "test_assembler.py").write_text("x = 1\n")
        refs = extract_references(_load("pytest.log"), cwd=tmp_path)
        assert len(refs) == 1
        assert refs[0].path == "tests/context/test_assembler.py"
        assert refs[0].line == 80
        assert refs[0].tool_hint == "pytest"

    def test_mypy(self, tmp_path):
        (tmp_path / "ollama_sentinel" / "context").mkdir(parents=True)
        (tmp_path / "ollama_sentinel" / "processor.py").write_text("x = 1\n")
        (tmp_path / "ollama_sentinel" / "context" / "assembler.py").write_text("x = 1\n")
        refs = extract_references(_load("mypy.log"), cwd=tmp_path)
        assert {r.path for r in refs} == {
            "ollama_sentinel/processor.py",
            "ollama_sentinel/context/assembler.py",
        }
        assert all(r.tool_hint == "mypy" for r in refs)

    def test_ruff(self, tmp_path):
        (tmp_path / "ollama_sentinel").mkdir()
        (tmp_path / "ollama_sentinel" / "models.py").write_text("x = 1\n")
        (tmp_path / "ollama_sentinel" / "utils.py").write_text("x = 1\n")
        refs = extract_references(_load("ruff.log"), cwd=tmp_path)
        assert {r.path for r in refs} == {
            "ollama_sentinel/models.py",
            "ollama_sentinel/utils.py",
        }
        assert all(r.tool_hint == "ruff" for r in refs)

    def test_mixed_tools_in_one_log(self, tmp_path):
        (tmp_path / "tests" / "context").mkdir(parents=True)
        (tmp_path / "ollama_sentinel").mkdir()
        (tmp_path / "tests" / "context" / "test_assembler.py").write_text("x = 1\n")
        (tmp_path / "ollama_sentinel" / "processor.py").write_text("x = 1\n")
        refs = extract_references(_load("mixed.log"), cwd=tmp_path)
        hints = {r.tool_hint for r in refs}
        assert "pytest" in hints and "traceback" in hints

    def test_generic_fallback(self, tmp_path):
        (tmp_path / "x.py").write_text("a = 1\n")
        text = "some log line mentions x.py:17 inline."
        refs = extract_references(text, cwd=tmp_path)
        assert any(r.tool_hint == "generic" and r.line == 17 for r in refs)


class TestExtractorSafety:
    def test_path_traversal_dropped(self, tmp_path):
        text = 'File "../../etc/passwd", line 1, in main'
        refs = extract_references(text, cwd=tmp_path)
        assert refs == []

    def test_nonexistent_paths_dropped(self, tmp_path):
        text = 'File "nonexistent/file.py", line 10, in main'
        refs = extract_references(text, cwd=tmp_path)
        assert refs == []

    def test_dedup_same_file_line(self, tmp_path):
        (tmp_path / "x.py").write_text("a = 1\n")
        text = (
            'File "x.py", line 42, in main\n'
            'File "x.py", line 42, in main\n'
        )
        refs = extract_references(text, cwd=tmp_path)
        assert len(refs) == 1

    def test_cap_at_50_references(self, tmp_path):
        (tmp_path / "x.py").write_text("a = 1\n")
        lines = [f'File "x.py", line {i}, in main' for i in range(1, 101)]
        refs = extract_references("\n".join(lines), cwd=tmp_path)
        assert len(refs) == 50

    def test_empty_input(self, tmp_path):
        assert extract_references("", cwd=tmp_path) == []

    def test_no_cwd_defaults_to_cwd(self):
        refs = extract_references("no paths here")
        assert refs == []
