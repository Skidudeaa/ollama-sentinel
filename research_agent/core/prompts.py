"""Prompt-construction helpers for research_agent workflow nodes.

Kept as a leaf module with no heavy imports (no langchain, no langgraph,
no playwright) so the helpers stay testable in environments that don't
install the [research] extras.
"""
from __future__ import annotations

from typing import List

from research_agent.tools.memory import WebPage


def _format_similar_pages_block(pages: List[WebPage]) -> str:
    """Render recalled webpages into a labeled prompt block.

    Returns "" when pages is empty so callers can splice the result into
    an f-string without adding a stray header. Each page becomes one line:
    "- {label} ({url})" when label != url, else "- {label}". Title is the
    preferred label, falling back to url, then to "(untitled)". Labels
    longer than 120 chars are truncated with "..." so the prompt stays
    one-line-per-page.
    """
    if not pages:
        return ""
    lines = []
    for p in pages:
        title = (p.title or "").strip()
        url = (p.url or "").strip()
        label = title or url or "(untitled)"
        if len(label) > 120:
            label = label[:117] + "..."
        if url and label != url:
            lines.append(f"- {label} ({url})")
        else:
            lines.append(f"- {label}")
    return "Relevant pages from prior research:\n" + "\n".join(lines)
