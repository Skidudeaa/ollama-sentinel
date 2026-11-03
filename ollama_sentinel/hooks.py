"""Git hook scripts and installers for ollama-sentinel (v0.2 Piece 2).

The post-commit hook links each commit to open Findings in the files it
touched, recording the commit SHA on those Findings for later Incident
attribution. Findings still come only from the model; this just stamps
``triggering_commit_sha`` so a future test failure (Piece 4) can build
the causal chain.
"""

import pathlib
import stat
from typing import List, Optional

import git

from .violation_db import ViolationDB

_POST_COMMIT_HOOK = """\
#!/bin/sh
# Installed by ollama-sentinel install-hooks.
# Links the commit to open Findings in the files it touched.
ollama-sentinel record-commit
"""

_PRE_COMMIT_HOOK_TEMPLATE = """\
#!/bin/sh
# Installed by ollama-sentinel install-hooks --pre-commit.
# Reviews staged files and blocks the commit when findings meet --fail-on.
ollama-sentinel check --staged --fail-on {fail_on}
"""

_VALID_SEVERITIES = ("critical", "high", "medium", "low")


def install_hooks(
    repo_path,
    *,
    pre_commit: bool = False,
    fail_on: str = "high",
) -> List[str]:
    """Install git hooks into ``repo_path/.git/hooks/``.

    Always installs ``post-commit`` (unless already present). When
    *pre_commit* is ``True``, also installs ``pre-commit`` configured to run
    ``ollama-sentinel check --staged --fail-on <fail_on>``.

    Returns the list of hook names installed. Existing hooks are left
    untouched (and not counted) — we never clobber a user's own hook.
    Raises ``FileNotFoundError`` if ``repo_path`` is not a git repository, or
    ``ValueError`` if *fail_on* is not a valid severity.
    """
    norm_fail_on = fail_on.strip().lower()
    if norm_fail_on not in _VALID_SEVERITIES:
        raise ValueError(
            f"Invalid --fail-on severity {fail_on!r}; "
            f"expected one of {', '.join(_VALID_SEVERITIES)}"
        )

    hooks_dir = pathlib.Path(repo_path) / ".git" / "hooks"
    if not hooks_dir.is_dir():
        raise FileNotFoundError(f"Not a git repository: {hooks_dir} missing")

    installed: List[str] = []

    post_commit = hooks_dir / "post-commit"
    if not post_commit.exists():
        post_commit.write_text(_POST_COMMIT_HOOK)
        post_commit.chmod(
            post_commit.stat().st_mode
            | stat.S_IXUSR
            | stat.S_IXGRP
            | stat.S_IXOTH
        )
        installed.append("post-commit")

    if pre_commit:
        pre_commit_path = hooks_dir / "pre-commit"
        if not pre_commit_path.exists():
            pre_commit_path.write_text(
                _PRE_COMMIT_HOOK_TEMPLATE.format(fail_on=norm_fail_on)
            )
            pre_commit_path.chmod(
                pre_commit_path.stat().st_mode
                | stat.S_IXUSR
                | stat.S_IXGRP
                | stat.S_IXOTH
            )
            installed.append("pre-commit")

    return installed


def record_commit(
    repo_path,
    db: ViolationDB,
    *,
    commit_sha: Optional[str] = None,
) -> int:
    """Link a commit to open Findings in the files it touched.

    Resolves ``commit_sha`` (or ``HEAD`` if None) via GitPython, extracts
    the touched file paths, and calls
    ``db.link_commit_to_findings``. Returns the number of Findings linked
    (0 if the commit touches no files with open Findings — a clean no-op).
    """
    repo = git.Repo(repo_path)
    commit = repo.commit(commit_sha) if commit_sha else repo.head.commit
    touched = list(commit.stats.files.keys())
    return db.link_commit_to_findings(commit.hexsha, touched)
