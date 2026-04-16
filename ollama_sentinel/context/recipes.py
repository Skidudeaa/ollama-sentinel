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
