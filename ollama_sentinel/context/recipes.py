"""Named recipes for the two consumers of the context assembler.

Each recipe encodes the section list, budget ratios, and retriever wiring
for its module. Consumers call one function; they do not hand-assemble.
"""
from __future__ import annotations

import collections
import hashlib
import pathlib
from typing import Dict, List, Optional, Sequence

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


_LANG_FENCE: Dict[str, str] = {
    ".py": "py", ".rb": "rb", ".ts": "ts", ".tsx": "tsx", ".js": "js",
    ".jsx": "jsx", ".go": "go", ".rs": "rs", ".java": "java",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".sh": "bash",
    ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".toml": "toml",
    ".md": "markdown",
}


def _fence_for(path: pathlib.Path) -> str:
    return _LANG_FENCE.get(path.suffix.lower(), "")


def _render_referenced_file(
    abs_path: pathlib.Path, rel_path: str, line_numbers: Sequence[int],
) -> str:
    """Render a referenced file: header + windowed-or-whole fenced excerpt."""
    lines = abs_path.read_text(errors="replace").splitlines()
    total = len(lines) or 1
    sorted_refs = sorted(n for n in set(line_numbers) if 1 <= n <= total)
    if not sorted_refs:
        # All references are out-of-range; fall back to rendering line 1 (which
        # triggers the whole-file branch if the file is short enough).
        sorted_refs = [1]
    refs_display = ", ".join(str(n) for n in sorted_refs)

    window_start = max(1, min(sorted_refs) - 8)
    window_end = min(total, max(sorted_refs) + 8)
    window_len = window_end - window_start + 1

    fence = _fence_for(abs_path)
    header = f"-- {rel_path} (referenced at lines {refs_display}) --"

    if window_len >= int(total * 0.8):
        body = "\n".join(lines)
    else:
        body_lines = []
        for i in range(window_start, window_end + 1):
            body_lines.append(f"{i:04d}|{lines[i - 1]}")
        body = "\n".join(body_lines)

    return f"{header}\n```{fence}\n{body}\n```"


async def build_triage_context(
    *,
    tool_output: str,
    references: Sequence,  # Sequence[Reference] — duck-typed to avoid import cycle
    explicit_context_files: Sequence[pathlib.Path],
    counter: TokenCounter,
    total_budget: int,
    cwd: pathlib.Path,
) -> str:
    """Triage recipe — assembles tool output + referenced source excerpts.

    `references` elements must have .path, .line, .tool_hint attributes (as
    produced by ollama_sentinel.triage.extractor.Reference).
    """
    sections: List[Section] = [
        Section(
            name="TOOL OUTPUT",
            items=[tool_output],
            priority=Priority.MUST_FIT,
            soft_budget=int(total_budget * 0.35),
            truncate="head",
        ),
    ]

    # --- REFERENCED SOURCE: one item per unique file, ranked by mention count.
    if references:
        by_file: Dict[str, List[int]] = collections.defaultdict(list)
        raw_path_by_file: Dict[str, str] = {}
        for ref in references:
            line = getattr(ref, "line", None)
            raw_path = getattr(ref, "path", None)
            if line is None or raw_path is None:
                continue
            candidate = pathlib.Path(raw_path)
            if not candidate.is_absolute():
                candidate = cwd / candidate
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            key = str(resolved)
            by_file[key].append(line)
            raw_path_by_file.setdefault(key, raw_path)

        ranked = sorted(
            by_file.items(),
            key=lambda kv: (-len(kv[1]), kv[0]),  # freq desc, path asc tiebreak
        )
        ref_items: List[ContextItem] = []
        for abs_key, line_list in ranked:
            abs_path = pathlib.Path(abs_key)
            if not abs_path.is_file():
                continue
            rel = raw_path_by_file[abs_key]
            body = _render_referenced_file(abs_path, rel, line_list)
            ref_items.append(ContextItem(text=body, embed_key=f"src:{abs_key}"))
        if ref_items:
            sections.append(Section(
                name="REFERENCED SOURCE",
                items=ref_items,
                priority=Priority.OPTIONAL,
                soft_budget=int(total_budget * 0.45),
                truncate="tail",
            ))

    # --- USER-PROVIDED CONTEXT: whole files, order preserved.
    if explicit_context_files:
        user_items: List[ContextItem] = []
        for p in explicit_context_files:
            if not p.is_file():
                continue
            fence = _fence_for(p)
            body = p.read_text(errors="replace")
            try:
                rel = str(p.relative_to(cwd)) if p.is_absolute() else str(p)
            except ValueError:
                rel = str(p)
            text = f"-- {rel} --\n```{fence}\n{body}\n```"
            user_items.append(ContextItem(text=text, embed_key=f"user:{p}"))
        if user_items:
            sections.append(Section(
                name="USER-PROVIDED CONTEXT",
                items=user_items,
                priority=Priority.OPTIONAL,
                soft_budget=int(total_budget * 0.20),
                truncate="tail",
            ))

    return await assemble(
        sections, total_budget=total_budget, counter=counter, query=tool_output,
    )
