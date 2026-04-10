"""Edge-case tests targeting specific review findings."""
import os
import pathlib

import pytest

from ollama_sentinel.utils import safe_read, chunk_content_by_lines


class TestSafeReadIntermediateSymlinks:
    """Test that intermediate directory symlinks are handled safely."""

    def test_intermediate_symlink_directory_blocked(self, tmp_path):
        """A symlink directory in the middle of the path should not escape watch_dir."""
        watch_dir = tmp_path / "watched"
        watch_dir.mkdir()

        # Create a target outside the watch dir
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("TOP SECRET")

        # Create a symlink DIRECTORY inside watch_dir pointing outside
        link_dir = watch_dir / "escape"
        link_dir.symlink_to(outside)

        # The file path goes through the symlink
        evil_path = watch_dir / "escape" / "secret.txt"

        # After resolve(), the path is outside watch_dir
        # safe_read should block this
        result = safe_read(evil_path, watch_dir)
        assert result == "", "Intermediate symlink directory should be blocked"

    def test_deeply_nested_symlink_escape(self, tmp_path):
        """Deeply nested symlink that escapes watch_dir."""
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        (watch_dir / "src").mkdir()

        outside = tmp_path / "secrets"
        outside.mkdir()
        (outside / "key.txt").write_text("API_KEY=hunter2")

        # Symlink deep inside the tree
        link = watch_dir / "src" / "vendor"
        link.symlink_to(outside)

        result = safe_read(watch_dir / "src" / "vendor" / "key.txt", watch_dir)
        assert result == ""


class TestChunkContentEdgeCases:
    """Additional edge cases for chunking."""

    def test_all_lines_preserved_in_large_file(self):
        """Generate a realistic multi-chunk file and verify no lines are lost."""
        lines = [f"def function_{i}():\n    return {i}\n\n" for i in range(200)]
        content = "".join(lines)

        chunks = chunk_content_by_lines(content, max_chars=500, overlap=50)

        # Reconstruct: collect all unique lines across all chunks
        all_lines = set()
        for chunk in chunks:
            for line in chunk.splitlines(True):
                all_lines.add(line)

        # Every original line must appear in at least one chunk
        for line in content.splitlines(True):
            assert line in all_lines, f"Line lost during chunking: {line!r}"

    def test_single_character_lines(self):
        """Content made of single-character lines should chunk correctly."""
        content = "\n".join("x" for _ in range(100)) + "\n"
        chunks = chunk_content_by_lines(content, max_chars=20, overlap=5)
        assert len(chunks) > 1
        # Reconstruct
        all_chars = "".join(chunks)
        # All 'x' chars from original should be present
        assert all_chars.count("x") >= content.count("x")

    def test_chunk_boundaries_dont_split_mid_line(self):
        """Chunks should break at line boundaries, not mid-line."""
        lines = [f"line-{i:04d}-{'x' * 40}\n" for i in range(50)]
        content = "".join(lines)

        chunks = chunk_content_by_lines(content, max_chars=200, overlap=0)

        for i, chunk in enumerate(chunks):
            # Every chunk except possibly the last should end with newline
            if i < len(chunks) - 1:
                assert chunk.endswith("\n"), f"Chunk {i} doesn't end at line boundary"


class TestSafeReadPermissions:
    """Test safe_read with various permission scenarios."""

    @pytest.mark.skipif(os.getuid() == 0, reason="Cannot test permission denial as root")
    def test_directory_instead_of_file(self, tmp_path):
        """Attempting to read a directory returns empty string."""
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        subdir = watch_dir / "not_a_file"
        subdir.mkdir()

        result = safe_read(subdir, watch_dir)
        assert result == ""

    def test_file_with_special_characters_in_name(self, tmp_path):
        """Files with spaces and special chars in names are read correctly."""
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        special = watch_dir / "file with spaces (1).py"
        special.write_text("# special file")

        result = safe_read(special, watch_dir)
        assert result == "# special file"

    def test_empty_file(self, tmp_path):
        """Empty file returns empty string (not an error)."""
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        empty = watch_dir / "empty.py"
        empty.write_text("")

        result = safe_read(empty, watch_dir)
        assert result == ""


class TestOutputDirectoryIgnoreEdgeCases:
    """Edge cases for the _should_ignore output directory check."""

    def test_file_named_like_output_dir_not_ignored(self, config_yaml_path, tmp_path):
        """A file named '.ollama_reviews.py' should NOT be ignored."""
        from ollama_sentinel.watcher import FileSentinel

        sentinel = FileSentinel(config_yaml_path)
        # Create a file that contains the output dir name but isn't IN it
        tricky_file = tmp_path / ".ollama_reviews.py"
        tricky_file.write_text("# not a review")

        # The filename contains ".ollama_reviews" but as a file, not directory component
        # path.parts would be (..., '.ollama_reviews.py') which doesn't match '.ollama_reviews'
        assert not sentinel._should_ignore(tricky_file)
