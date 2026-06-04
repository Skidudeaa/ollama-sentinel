"""
Tests for ollama_sentinel.utils module.
"""
import gzip
import os
import pathlib

import pytest

from ollama_sentinel.utils import (
    chunk_content_by_lines,
    generate_diff,
    read_strict,
    safe_read,
    safe_write,
    save_compressed,
)


# ---------------------------------------------------------------------------
# safe_read
# ---------------------------------------------------------------------------


class TestSafeRead:
    """Tests for safe_read: symlink protection, path traversal, containment."""

    def test_normal_file_inside_watch_dir(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        f = watch_dir / "hello.py"
        f.write_text("print('hello')")

        assert safe_read(f, watch_dir) == "print('hello')"

    def test_file_in_subdirectory(self, tmp_path):
        watch_dir = tmp_path / "project"
        sub = watch_dir / "pkg"
        sub.mkdir(parents=True)
        f = sub / "mod.py"
        f.write_text("x = 1")

        assert safe_read(f, watch_dir) == "x = 1"

    def test_symlink_returns_empty(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        target = watch_dir / "real.py"
        target.write_text("secret")
        link = watch_dir / "link.py"
        os.symlink(target, link)

        assert safe_read(link, watch_dir) == ""

    def test_path_traversal_with_dotdot(self, tmp_path):
        """Path containing '..' that escapes watch_dir must return ''."""
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("password123")

        # Construct a traversal path: project/../outside/secret.txt
        traversal = watch_dir / ".." / "outside" / "secret.txt"
        assert safe_read(traversal, watch_dir) == ""

    def test_sibling_directory_with_matching_prefix(self, tmp_path):
        """
        Regression: watch_dir='/tmp/proj', path='/tmp/project_evil/secret.py'
        must NOT pass containment. The old string-prefix approach would allow
        this; relative_to correctly rejects it.
        """
        watch_dir = tmp_path / "proj"
        watch_dir.mkdir()
        evil_dir = tmp_path / "project_evil"
        evil_dir.mkdir()
        secret = evil_dir / "secret.py"
        secret.write_text("import os; os.system('rm -rf /')")

        assert safe_read(secret, watch_dir) == ""

    def test_file_outside_watch_dir_entirely(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        f = other / "data.txt"
        f.write_text("external data")

        assert safe_read(f, watch_dir) == ""

    def test_nonexistent_file(self, tmp_path):
        """File does not exist but path is inside watch_dir."""
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        missing = watch_dir / "no_such_file.py"

        assert safe_read(missing, watch_dir) == ""

    def test_unreadable_file_returns_empty(self, tmp_path):
        """File exists but cannot be read (permission denied)."""
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        f = watch_dir / "locked.py"
        f.write_text("content")
        f.chmod(0o000)

        try:
            assert safe_read(f, watch_dir) == ""
        finally:
            f.chmod(0o644)  # restore so tmp_path cleanup works

    def test_binary_file_with_replace_errors(self, tmp_path):
        """Binary content should be returned with replacement chars, not crash."""
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        f = watch_dir / "data.bin"
        f.write_bytes(b"\x80\x81\x82hello\xff")

        result = safe_read(f, watch_dir)
        assert "hello" in result
        assert result != ""


# ---------------------------------------------------------------------------
# read_strict
# ---------------------------------------------------------------------------


class TestReadStrict:
    """read_strict: containment like safe_read, but raises on failure and
    refuses non-UTF-8 (never lossy-decodes)."""

    def test_reads_utf8_file(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        f = watch_dir / "u.py"
        f.write_text("x = 'café'\n", encoding="utf-8")
        assert read_strict(f, watch_dir) == "x = 'café'\n"

    def test_non_utf8_file_raises(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        f = watch_dir / "data.py"
        f.write_bytes(b"x = 1  # \x80\x81\xff not utf-8\n")
        with pytest.raises((UnicodeDecodeError, ValueError)):
            read_strict(f, watch_dir)

    def test_symlink_raises(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        target = watch_dir / "real.py"
        target.write_text("secret")
        link = watch_dir / "link.py"
        os.symlink(target, link)
        with pytest.raises(ValueError):
            read_strict(link, watch_dir)

    def test_traversal_raises(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("password")
        traversal = watch_dir / ".." / "outside" / "secret.txt"
        with pytest.raises(ValueError):
            read_strict(traversal, watch_dir)

    def test_preserves_crlf_line_endings(self, tmp_path):
        # Universal-newline translation would collapse CRLF to LF on read; the
        # write path must preserve the file's original endings so a later
        # write-back does not silently flip every line.
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        f = watch_dir / "win.py"
        f.write_bytes(b"a = 1\r\nb = 2\r\n")
        assert read_strict(f, watch_dir) == "a = 1\r\nb = 2\r\n"


# ---------------------------------------------------------------------------
# safe_write
# ---------------------------------------------------------------------------


class TestSafeWrite:
    """safe_write: atomic, contained, UTF-8, mode-preserving, symlink-rejecting."""

    def test_writes_and_reads_back(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        f = watch_dir / "out.py"
        f.write_text("old\n")
        safe_write(f, "new content\n", watch_dir)
        assert f.read_text(encoding="utf-8") == "new content\n"

    def test_replaces_existing_file_atomically(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        f = watch_dir / "mod.py"
        f.write_text("v1\n")
        safe_write(f, "v2\n", watch_dir)
        assert f.read_text(encoding="utf-8") == "v2\n"
        # no leftover temp files in the directory
        assert [p.name for p in watch_dir.iterdir()] == ["mod.py"]

    def test_preserves_mode(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        f = watch_dir / "script.py"
        f.write_text("#!/usr/bin/env python\n")
        f.chmod(0o755)
        safe_write(f, "#!/usr/bin/env python\nprint('hi')\n", watch_dir)
        assert (os.stat(f).st_mode & 0o777) == 0o755

    def test_writes_utf8(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        f = watch_dir / "u.py"
        f.write_text("x\n")
        safe_write(f, "y = 'café'\n", watch_dir)
        assert f.read_bytes() == "y = 'café'\n".encode("utf-8")

    def test_symlink_target_raises(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        target = watch_dir / "real.py"
        target.write_text("x")
        link = watch_dir / "link.py"
        os.symlink(target, link)
        with pytest.raises(ValueError):
            safe_write(link, "nope", watch_dir)
        # target untouched
        assert target.read_text() == "x"

    def test_traversal_raises(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        victim = outside / "secret.txt"
        victim.write_text("password")
        traversal = watch_dir / ".." / "outside" / "secret.txt"
        with pytest.raises(ValueError):
            safe_write(traversal, "owned", watch_dir)
        assert victim.read_text() == "password"

    def test_creates_parent_dirs_within_watch_dir(self, tmp_path):
        watch_dir = tmp_path / "project"
        watch_dir.mkdir()
        f = watch_dir / "pkg" / "sub" / "new.py"
        safe_write(f, "x = 1\n", watch_dir)
        assert f.read_text(encoding="utf-8") == "x = 1\n"


# ---------------------------------------------------------------------------
# chunk_content_by_lines
# ---------------------------------------------------------------------------


class TestChunkContentByLines:
    """Tests for chunk_content_by_lines: splitting, overlap, completeness."""

    def test_short_content_single_chunk(self):
        content = "one line\n"
        chunks = chunk_content_by_lines(content, max_chars=100, overlap=10)
        assert chunks == [content]

    def test_empty_content(self):
        chunks = chunk_content_by_lines("", max_chars=100, overlap=10)
        assert chunks == [""]

    def test_content_exactly_at_max_chars(self):
        content = "a" * 50
        chunks = chunk_content_by_lines(content, max_chars=50, overlap=10)
        assert chunks == [content]

    def test_splits_into_multiple_chunks(self):
        lines = [f"line {i}\n" for i in range(20)]
        content = "".join(lines)
        # Each line is ~7-8 chars; set max_chars so we need multiple chunks
        chunks = chunk_content_by_lines(content, max_chars=40, overlap=0)
        assert len(chunks) > 1
        # All chunks joined should equal original (no overlap to remove)
        assert "".join(chunks) == content

    def test_no_lines_dropped_at_boundaries(self):
        """
        Regression: a previous variable-shadowing bug caused lines at chunk
        boundaries to be silently dropped.  Verify every original line appears
        in at least one chunk.
        """
        lines = [f"line-{i:03d}\n" for i in range(100)]
        content = "".join(lines)
        chunks = chunk_content_by_lines(content, max_chars=80, overlap=20)

        # Collect every line present in any chunk
        found_lines = set()
        for chunk in chunks:
            for line in chunk.splitlines(True):
                found_lines.add(line)

        original_lines = set(lines)
        assert original_lines == found_lines, (
            f"Missing lines: {original_lines - found_lines}"
        )

    def test_overlap_between_consecutive_chunks(self):
        """
        The last `overlap` characters of chunk[i] should appear at the
        start of chunk[i+1].
        """
        lines = [f"L{i:02d}|" + "x" * 10 + "\n" for i in range(30)]
        content = "".join(lines)
        overlap = 30
        chunks = chunk_content_by_lines(content, max_chars=60, overlap=overlap)

        assert len(chunks) >= 2, "Need at least 2 chunks to test overlap"

        for i in range(len(chunks) - 1):
            tail = chunks[i][-overlap:]
            # chunk[i+1] must start with some suffix of chunk[i]
            # Because overlap is line-aware, the overlap region consists of
            # whole lines whose total length <= overlap.  Find those lines.
            overlap_lines = []
            overlap_size = 0
            for line in reversed(chunks[i].splitlines(True)):
                if overlap_size + len(line) > overlap:
                    break
                overlap_lines.insert(0, line)
                overlap_size += len(line)
            overlap_text = "".join(overlap_lines)
            if overlap_text:
                assert chunks[i + 1].startswith(overlap_text), (
                    f"Chunk {i+1} should start with overlap text from chunk {i}"
                )

    def test_single_very_long_line(self):
        """A single line longer than max_chars is kept as one chunk."""
        content = "x" * 500 + "\n"
        chunks = chunk_content_by_lines(content, max_chars=100, overlap=20)
        assert len(chunks) == 1
        assert chunks[0] == content

    def test_all_content_preserved_after_removing_overlap(self):
        """
        Reconstruct original content by removing overlapping prefixes
        from chunks 1..N and verify it matches the original.
        """
        lines = [f"data-{i}\n" for i in range(50)]
        content = "".join(lines)
        overlap = 25
        chunks = chunk_content_by_lines(content, max_chars=60, overlap=overlap)

        # Rebuild: take full first chunk, then for each subsequent chunk
        # strip the overlapping prefix.
        rebuilt = chunks[0]
        for i in range(1, len(chunks)):
            # Find how much of chunks[i] overlaps with what we already have
            prev_chunk = chunks[i - 1]
            overlap_lines = []
            overlap_size = 0
            for line in reversed(prev_chunk.splitlines(True)):
                if overlap_size + len(line) > overlap:
                    break
                overlap_lines.insert(0, line)
                overlap_size += len(line)
            overlap_text = "".join(overlap_lines)
            if chunks[i].startswith(overlap_text):
                rebuilt += chunks[i][len(overlap_text):]
            else:
                rebuilt += chunks[i]

        assert rebuilt == content

    def test_zero_overlap(self):
        lines = [f"row {i}\n" for i in range(20)]
        content = "".join(lines)
        chunks = chunk_content_by_lines(content, max_chars=30, overlap=0)
        assert "".join(chunks) == content

    def test_overlap_larger_than_chunk(self):
        """Overlap larger than max_chars should not crash."""
        content = "a\nb\nc\nd\ne\n"
        chunks = chunk_content_by_lines(content, max_chars=4, overlap=100)
        # All lines must still be present
        found = set()
        for c in chunks:
            for line in c.splitlines(True):
                found.add(line)
        for line in content.splitlines(True):
            assert line in found


# ---------------------------------------------------------------------------
# generate_diff
# ---------------------------------------------------------------------------


class TestGenerateDiff:
    """Tests for generate_diff: unified diff output."""

    def test_identical_strings_produce_empty_diff(self):
        text = "line 1\nline 2\nline 3"
        result = generate_diff(text, text, "2026-01-01T00:00:00")
        assert result == ""

    def test_added_lines(self):
        previous = "line 1\nline 2"
        current = "line 1\nline 2\nline 3"
        result = generate_diff(previous, current, "2026-01-01T00:00:00")

        assert "+line 3" in result
        assert "--- Previous Review" in result
        assert "+++ Current Review (2026-01-01T00:00:00)" in result

    def test_removed_lines(self):
        previous = "line 1\nline 2\nline 3"
        current = "line 1\nline 3"
        result = generate_diff(previous, current, "2026-01-01T00:00:00")

        assert "-line 2" in result

    def test_modified_line(self):
        previous = "hello world"
        current = "hello universe"
        result = generate_diff(previous, current, "2026-01-01T00:00:00")

        assert "-hello world" in result
        assert "+hello universe" in result

    def test_empty_to_content(self):
        result = generate_diff("", "new content", "ts")
        assert "+new content" in result

    def test_content_to_empty(self):
        result = generate_diff("some content", "", "ts")
        assert "-some content" in result

    def test_timestamp_in_header(self):
        result = generate_diff("a", "b", "2026-04-09T12:00:00")
        assert "2026-04-09T12:00:00" in result


# ---------------------------------------------------------------------------
# save_compressed
# ---------------------------------------------------------------------------


class TestSaveCompressed:
    """Tests for save_compressed: gzip file creation."""

    def test_writes_gzip_readable_file(self, tmp_path):
        out = tmp_path / "review.md.gz"
        content = "# Review\n\nLooks good!"
        save_compressed(out, content)

        assert out.exists()
        with gzip.open(out, "rt", encoding="utf-8") as f:
            assert f.read() == content

    def test_roundtrip_large_content(self, tmp_path):
        out = tmp_path / "big.gz"
        content = "x" * 100_000 + "\n" + "y" * 100_000
        save_compressed(out, content)

        with gzip.open(out, "rt", encoding="utf-8") as f:
            assert f.read() == content

    def test_unicode_content(self, tmp_path):
        out = tmp_path / "unicode.gz"
        content = "Review: excellent \u2714\nIssue: \u26a0 potential bug\n\u00e9\u00e8\u00ea\u00eb"
        save_compressed(out, content)

        with gzip.open(out, "rt", encoding="utf-8") as f:
            assert f.read() == content

    def test_empty_content(self, tmp_path):
        out = tmp_path / "empty.gz"
        save_compressed(out, "")

        with gzip.open(out, "rt", encoding="utf-8") as f:
            assert f.read() == ""

    def test_fallback_on_bad_path(self, tmp_path):
        """
        If gzip write fails (e.g. directory doesn't exist), the function
        falls back to writing an uncompressed file with .gz stripped.
        """
        bad_dir = tmp_path / "nonexistent"
        # Do NOT create bad_dir
        out = bad_dir / "review.md.gz"

        # This should attempt gzip, fail, then try the fallback path.
        # The fallback path will also fail since the directory doesn't exist.
        # The function doesn't raise -- it logs and tries fallback.
        # We just verify it doesn't raise.
        with pytest.raises(Exception):
            # The fallback itself will raise because the directory is missing
            save_compressed(out, "content")
