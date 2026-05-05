"""Thin integration bridge between the sentinel CLI and research_agent.

All interaction with the research_agent package is isolated here so that
the sentinel CLI works cleanly even when [research] extras are not installed.
"""
from __future__ import annotations

import json
import logging
import pathlib
from typing import Optional

log = logging.getLogger(__name__)


def is_available() -> bool:
    """Check if the [research] extras are installed and importable."""
    try:
        import research_agent.core.agent  # noqa: F401
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def run_query(
    query: str,
    repo_path: pathlib.Path,
    config_path: Optional[pathlib.Path] = None,
    code_context: Optional[str] = None,
) -> dict:
    """Run a research query and return a serializable result dict.

    Raises ImportError if [research] extras are missing.
    """
    from research_agent.core.agent import ResearchAgent
    from research_agent.core.config import Config

    Config.reset()

    agent = ResearchAgent(
        repo_path=str(repo_path),
        config_path=str(config_path) if config_path else None,
    )
    session = agent.research(query=query, code_context=code_context)

    return {
        "query": session.query,
        "answer": session.answer,
        "confidence": session.confidence,
        "timestamp": session.end_time,
        "duration_s": session.duration,
        "source_count": len(session.sources),
    }


def run_interactive(
    repo_path: pathlib.Path,
    config_path: Optional[pathlib.Path] = None,
) -> None:
    """Launch the interactive research REPL.

    Raises ImportError if [research] extras are missing.
    """
    from research_agent.core.agent import ResearchAgent
    from research_agent.core.config import Config
    from research_agent.cli.interface import run_cli

    Config.reset()

    agent = ResearchAgent(
        repo_path=str(repo_path),
        config_path=str(config_path) if config_path else None,
    )
    run_cli(agent)


def persist_latest(result: dict, output_dir: pathlib.Path) -> pathlib.Path:
    """Write research result to output_dir/research/latest.json."""
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    path = research_dir / "latest.json"
    path.write_text(json.dumps(result, indent=2))
    return path


def load_latest(output_dir: pathlib.Path) -> Optional[dict]:
    """Load the latest research result, or None if unavailable."""
    path = output_dir / "research" / "latest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
