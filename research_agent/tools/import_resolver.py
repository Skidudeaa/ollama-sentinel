"""Back-compat re-export. The canonical home is now
``ollama_sentinel.context.import_resolver`` so the resolver is available
without the [research] extras. This module exists so existing
``from research_agent.tools.import_resolver import ImportResolver``
callers keep working until they migrate.
"""
from ollama_sentinel.context.import_resolver import ImportResolver  # noqa: F401

__all__ = ["ImportResolver"]
