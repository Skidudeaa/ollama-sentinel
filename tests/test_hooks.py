"""Tests for ollama_sentinel.hooks — v0.2 Piece 2.

Post-commit hook installer + record_commit (links a commit to open
Findings in the files it touched). Mirrors test_violation_db.py
conventions: tmp_path, try/finally db.close(), real code (real git
repos via GitPython, no mocks).
"""

import os
import stat

import git
import pytest
from typer.testing import CliRunner

from ollama_sentinel.violation_db import Finding, ViolationDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_repo(path):
    """Init a real git repo at *path* with commit identity configured."""
    repo = git.Repo.init(path)
    cw = repo.config_writer()
    cw.set_value("user", "name", "Test")
    cw.set_value("user", "email", "test@example.com")
    cw.release()
    return repo


def _commit_file(repo, repo_path, rel_path, content="x = 1\n"):
    """Write rel_path under the repo, stage it, commit; return the SHA."""
    fp = os.path.join(repo_path, rel_path)
    os.makedirs(os.path.dirname(fp), exist_ok=True) if os.path.dirname(fp) else None
    with open(fp, "w") as fh:
        fh.write(content)
    repo.index.add([rel_path])
    return repo.index.commit(f"add {rel_path}").hexsha


def _make_finding(**overrides) -> Finding:
    defaults = dict(
        file_path="src/app.py",
        line_start=10,
        line_end=12,
        category="bug",
        severity="high",
        description="Possible null dereference",
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _seed_finding_id(db, **overrides) -> int:
    f = _make_finding(**overrides)
    db.persist_findings(f.file_path, [f])
    row = db._conn.execute(
        "SELECT id FROM findings WHERE file_path=? AND line_start=? "
        "AND line_end=? AND category=? ORDER BY id DESC LIMIT 1",
        (f.file_path, f.line_start, f.line_end, f.category),
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# install_hooks
# ---------------------------------------------------------------------------


class TestInstallHooks:
    def test_install_hooks_creates_post_commit(self, tmp_path):
        from ollama_sentinel.hooks import install_hooks

        _git_repo(tmp_path)
        installed = install_hooks(tmp_path)

        assert installed == ["post-commit"]
        hook = tmp_path / ".git" / "hooks" / "post-commit"
        assert hook.exists()
        assert "ollama-sentinel record-commit" in hook.read_text()
        # Executable bit set for the owner.
        assert hook.stat().st_mode & stat.S_IXUSR

    def test_install_hooks_does_not_overwrite_existing(self, tmp_path):
        from ollama_sentinel.hooks import install_hooks

        _git_repo(tmp_path)
        hook = tmp_path / ".git" / "hooks" / "post-commit"
        hook.write_text("#!/bin/sh\necho pre-existing\n")

        installed = install_hooks(tmp_path)

        assert installed == []  # nothing installed
        assert hook.read_text() == "#!/bin/sh\necho pre-existing\n"

    def test_install_hooks_rejects_non_repo(self, tmp_path):
        from ollama_sentinel.hooks import install_hooks

        with pytest.raises(FileNotFoundError):
            install_hooks(tmp_path)  # no .git/hooks here


# ---------------------------------------------------------------------------
# record_commit
# ---------------------------------------------------------------------------


class TestRecordCommit:
    def test_record_commit_links_findings_in_touched_files(self, tmp_path):
        from ollama_sentinel.hooks import record_commit

        repo = _git_repo(tmp_path)
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            fid = _seed_finding_id(db, file_path="src/c.py")
            sha = _commit_file(repo, str(tmp_path), "src/c.py")

            n = record_commit(tmp_path, db)

            assert n == 1
            row = db._conn.execute(
                "SELECT triggering_commit_sha FROM findings WHERE id=?", (fid,)
            ).fetchone()
            assert row[0] == sha
        finally:
            db.close()

    def test_record_commit_skips_resolved_findings(self, tmp_path):
        from ollama_sentinel.hooks import record_commit

        repo = _git_repo(tmp_path)
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            open_fid = _seed_finding_id(
                db, file_path="src/c.py", line_start=1, line_end=2
            )
            resolved_fid = _seed_finding_id(
                db, file_path="src/c.py", line_start=9, line_end=9
            )
            db.mark_resolved(resolved_fid)
            _commit_file(repo, str(tmp_path), "src/c.py")

            n = record_commit(tmp_path, db)

            assert n == 1
            assert (
                db._conn.execute(
                    "SELECT triggering_commit_sha FROM findings WHERE id=?",
                    (open_fid,),
                ).fetchone()[0]
                is not None
            )
            assert (
                db._conn.execute(
                    "SELECT triggering_commit_sha FROM findings WHERE id=?",
                    (resolved_fid,),
                ).fetchone()[0]
                is None
            )
        finally:
            db.close()

    def test_record_commit_no_findings_is_noop(self, tmp_path):
        from ollama_sentinel.hooks import record_commit

        repo = _git_repo(tmp_path)
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            _commit_file(repo, str(tmp_path), "untracked_by_findings.py")
            assert record_commit(tmp_path, db) == 0
        finally:
            db.close()

    def test_record_commit_explicit_sha(self, tmp_path):
        from ollama_sentinel.hooks import record_commit

        repo = _git_repo(tmp_path)
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            _seed_finding_id(db, file_path="src/c.py")
            sha1 = _commit_file(repo, str(tmp_path), "src/c.py")
            _commit_file(repo, str(tmp_path), "other.py")  # HEAD moves past sha1

            n = record_commit(tmp_path, db, commit_sha=sha1)

            assert n == 1  # links by the explicit (older) commit, not HEAD
        finally:
            db.close()


# ---------------------------------------------------------------------------
# CLI verbs (thin wrappers — kept TDD-pure)
# ---------------------------------------------------------------------------


class TestHookCLIVerbs:
    def _write_config(self, tmp_path):
        cfg = tmp_path / "ollama-sentinel.yaml"
        cfg.write_text(
            "ollama:\n"
            "  host: http://localhost:11434\n"
            "  models:\n"
            "    default:\n"
            "      name: qwen2.5-coder:7b\n"
            "      system_prompt: review this\n"
            f"watch:\n"
            f"  directory: {tmp_path}\n"
            "memory:\n"
            "  enabled: true\n"
            "  db_path: memory.db\n"
        )
        return cfg

    def test_install_hooks_cli_installs(self, tmp_path):
        from ollama_sentinel.cli import app

        _git_repo(tmp_path)
        cfg = self._write_config(tmp_path)
        result = CliRunner().invoke(
            app, ["install-hooks", "--config", str(cfg)]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".git" / "hooks" / "post-commit").exists()

    def test_record_commit_cli_links(self, tmp_path):
        from ollama_sentinel.cli import app

        repo = _git_repo(tmp_path)
        cfg = self._write_config(tmp_path)
        db = ViolationDB(str(tmp_path / "memory.db"))
        try:
            _seed_finding_id(db, file_path="src/c.py")
        finally:
            db.close()
        _commit_file(repo, str(tmp_path), "src/c.py")

        result = CliRunner().invoke(
            app, ["record-commit", "--config", str(cfg)]
        )
        assert result.exit_code == 0, result.output

        db2 = ViolationDB(str(tmp_path / "memory.db"))
        try:
            linked = db2._conn.execute(
                "SELECT triggering_commit_sha FROM findings "
                "WHERE file_path='src/c.py'"
            ).fetchone()[0]
            assert linked is not None
        finally:
            db2.close()
