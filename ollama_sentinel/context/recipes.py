"""Named recipes for the two consumers of the context assembler.

Each recipe encodes the section list, budget ratios, and retriever wiring
for its module. Consumers call one function; they do not hand-assemble.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional, Sequence

from ollama_sentinel.context.assembler import (
    ContextItem,
    Priority,
    Retriever,
    Section,
    assemble,
)
from ollama_sentinel.context.tokens import TokenCounter


def _render_file_block(
    content: Optional[str], diff: Optional[str], file_type: str
) -> str:
    if diff is not None:
        return f"```diff\n{diff}\n```"
    body = content if content is not None and content != "" else "<Empty File>"
    return f"```{file_type}\n{body}\n```"


def _render_violation(v: dict) -> str:
    count = v.get("occurrence_count", 1)
    first = (v.get("first_seen") or "unknown")[:10]
    severity = v.get("severity", "medium")
    category = v.get("category", "unknown")
    line = v.get("line_start", 0)
    desc = v.get("description", "")
    return f"- [{severity}] {category} at line {line}: {desc} (seen {count}x since {first})"


def _hash_violation(v: dict) -> str:
    """Stable fallback key for violations that lack an `id` field."""
    key = f"{v.get('file_path')}:{v.get('line_start')}:{v.get('category')}:{v.get('description')}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


async def build_review_context(
    *,
    file_rel_path: str,
    file_type: str,
    content: Optional[str],
    diff: Optional[str],
    chunk_info: str,
    prior_violations: Sequence[dict],
    counter: TokenCounter,
    total_budget: int,
    retriever: Retriever,
) -> str:
    """Sentinel recipe — replaces the body of FileProcessor.format_prompt."""
    sections: List[Section] = [
        Section(
            name=f"FILE: {file_rel_path}{chunk_info}",
            items=[_render_file_block(content, diff, file_type)],
            priority=Priority.MUST_FIT,
            soft_budget=int(total_budget * 0.70),
            truncate="tail",
        ),
    ]
    if prior_violations:
        violation_items = [
            ContextItem(
                text=_render_violation(v),
                embed_key=f"finding:{v.get('id', _hash_violation(v))}",
            )
            for v in prior_violations
        ]
        sections.append(Section(
            name="PRIOR UNRESOLVED ISSUES (address or escalate if still present)",
            items=violation_items,
            priority=Priority.OPTIONAL,
            soft_budget=int(total_budget * 0.25),
            retriever=retriever,
        ))

    return await assemble(
        sections,
        total_budget=total_budget,
        counter=counter,
        query=content if content else diff,
    )


def _format_impact_report(impact) -> str:
    """Inline impact report formatter (duplicated from research_agent.tools.synthesis
    to keep the context package independent of the research_agent package).

    `impact` is duck-typed: it must have .items (iterable of objects with
    .file_path, .line_number, .pattern, .severity, .action) and .affected_files.
    """
    items = getattr(impact, "items", []) or []
    affected = getattr(impact, "affected_files", []) or []
    high = [it for it in items if getattr(it, "severity", "") == "HIGH"]
    medium = [it for it in items if getattr(it, "severity", "") == "MEDIUM"]
    low = [it for it in items if getattr(it, "severity", "") == "LOW"]

    lines: List[str] = [
        f"{len(items)} call sites across {len(affected)} files",
        "",
    ]
    if high:
        lines.append("HIGH SEVERITY (breaking):")
        for it in high:
            lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {it.action}")
        lines.append("")
    if medium:
        lines.append("MEDIUM SEVERITY (deprecated):")
        for it in medium:
            action = it.action or "Review usage"
            lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {action}")
        lines.append("")
    if low:
        lines.append("LOW SEVERITY (changed):")
        for it in low:
            action = it.action or "Monitor for changes"
            lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {action}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _content_item_to_context_item(src) -> ContextItem:
    """Convert a research_agent ContentItem (duck-typed) into a ContextItem."""
    url = getattr(src, "url", "") or ""
    title = getattr(src, "title", "") or ""
    content = getattr(src, "content", "") or ""
    text = f"SOURCE: {url}\n{title}\n---\n{content}"
    if url:
        key = f"source:{hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]}"
    else:
        key = f"source:{hashlib.sha1(content[:256].encode('utf-8')).hexdigest()[:16]}"
    return ContextItem(text=text, embed_key=key)


async def build_research_context(
    *,
    query: str,
    web_sources: Sequence,
    code_results: Optional[str],
    impact,  # Optional[ImpactAnalysis] — duck-typed to keep packages decoupled
    counter: TokenCounter,
    total_budget: int,
    retriever: Retriever,
) -> str:
    """Research-agent recipe — replaces the 4000-char truncation in synthesis."""
    sections: List[Section] = []

    if impact is not None and getattr(impact, "items", None):
        sections.append(Section(
            name="IMPACT ANALYSIS",
            items=[_format_impact_report(impact)],
            priority=Priority.MUST_FIT,
            soft_budget=int(total_budget * 0.30),
            truncate="tail",
        ))

    if code_results:
        sections.append(Section(
            name="CODE CONTEXT",
            items=[code_results],
            priority=Priority.MUST_FIT,
            soft_budget=int(total_budget * 0.20),
            truncate="tail",
        ))

    if web_sources:
        sections.append(Section(
            name="WEB SOURCES",
            items=[_content_item_to_context_item(s) for s in web_sources],
            priority=Priority.OPTIONAL,
            soft_budget=int(total_budget * 0.45),
            retriever=retriever,
        ))

    return await assemble(
        sections, total_budget=total_budget, counter=counter, query=query,
    )
