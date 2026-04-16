"""Token-budgeted section assembler.

Pure module: no I/O, no Ollama calls, no tokenizer instantiation.
All dependencies are injected. `assemble()` is the only entrypoint.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Literal, Optional, Protocol, Sequence, Union

from ollama_sentinel.context.tokens import TokenCounter

log = logging.getLogger("ollama-sentinel")


class Priority(Enum):
    MUST_FIT = "must_fit"
    OPTIONAL = "optional"


@dataclass(frozen=True)
class ContextItem:
    text: str
    embed_key: str


@dataclass(frozen=True)
class Section:
    name: str
    items: Sequence[Union[str, ContextItem]]
    priority: Priority
    soft_budget: int
    retriever: Optional["Retriever"] = None
    truncate: Literal["head", "tail"] = "tail"


class Retriever(Protocol):
    async def rank(
        self, items: Sequence[ContextItem], query: str
    ) -> List[ContextItem]: ...


def chunk_by_lines(
    content: str,
    *,
    counter: TokenCounter,
    max_tokens: int,
    overlap_tokens: int = 0,
) -> List[str]:
    """Split content into chunks that each fit within max_tokens, preferring
    line boundaries. Adjacent chunks share `overlap_tokens` worth of trailing
    lines from the previous chunk.
    """
    if counter.count(content) <= max_tokens:
        return [content]

    lines = content.splitlines(keepends=True)
    chunks: List[str] = []
    current: List[str] = []
    current_tokens = 0

    for line in lines:
        line_tokens = counter.count(line)
        if current and current_tokens + line_tokens > max_tokens:
            chunks.append("".join(current))
            # Build overlap from trailing lines of the chunk we just emitted.
            overlap: List[str] = []
            overlap_t = 0
            for prev in reversed(current):
                t = counter.count(prev)
                if overlap_t + t > overlap_tokens:
                    break
                overlap.insert(0, prev)
                overlap_t += t
            current = overlap
            current_tokens = overlap_t
        current.append(line)
        current_tokens += line_tokens

    if current:
        chunks.append("".join(current))
    return chunks


# ---------------------------------------------------------------------------
# assemble() and private helpers
# ---------------------------------------------------------------------------

_TRUNCATED_SUFFIX = "\n… [truncated]"


async def assemble(
    sections: Sequence[Section],
    *,
    total_budget: int,
    counter: TokenCounter,
    query: Optional[str] = None,
) -> str:
    """Assemble sections into a single prompt-ready string under total_budget.

    MUST_FIT sections are always included and share the budget proportionally
    when their soft_budget sum exceeds total_budget. OPTIONAL sections fill
    whatever budget remains, dropped silently when there is no room.

    Callers inject ``counter`` and, via each Section, an optional ``retriever``.
    This function never raises; retriever failures are logged and fall back to
    identity ordering.
    """
    must = [s for s in sections if s.priority == Priority.MUST_FIT]
    opt = [s for s in sections if s.priority == Priority.OPTIONAL]

    # ------------------------------------------------------------------
    # 1. Reserve budget for MUST_FIT sections. Proportionally scale if
    #    the sum of soft_budgets already exceeds total_budget.
    # ------------------------------------------------------------------
    reserved = sum(s.soft_budget for s in must)
    if reserved > total_budget:
        log.warning(
            "Recipe bug: MUST_FIT sections total %s tokens but total_budget is %s; "
            "scaling proportionally.", reserved, total_budget,
        )
        scale = total_budget / reserved if reserved else 1.0
        must_budgets = {s.name: max(1, int(s.soft_budget * scale)) for s in must}
        reserved = sum(must_budgets.values())
    else:
        must_budgets = {s.name: s.soft_budget for s in must}

    remaining = max(0, total_budget - reserved)

    # ------------------------------------------------------------------
    # 2. Render MUST_FIT sections, truncating single items if needed.
    # ------------------------------------------------------------------
    rendered: List[str] = []
    for s in must:
        body = await _render_section(s, counter=counter, budget=must_budgets[s.name], query=query)
        if body:
            rendered.append(body)

    # ------------------------------------------------------------------
    # 3. Walk OPTIONAL sections in order, filling remaining budget.
    # ------------------------------------------------------------------
    for s in opt:
        if remaining <= 0:
            break
        used, body = await _render_optional_section(
            s, counter=counter, budget=min(s.soft_budget, remaining), query=query,
        )
        if body:
            rendered.append(body)
            remaining -= used

    return "\n\n".join(rendered)


async def _render_section(
    s: Section, *, counter: TokenCounter, budget: int, query: Optional[str]
) -> str:
    """Render a MUST_FIT section. Items are joined; overflow is truncated.

    The truncated suffix cost is subtracted from the text budget so that
    ``header + text + suffix`` together never exceed ``budget``.
    """
    if not s.items:
        return ""

    header = f"{s.name}:"
    header_tokens = counter.count(header + "\n")
    body_budget = max(0, budget - header_tokens)
    if body_budget == 0:
        return ""

    joined = "\n".join(_item_text(i) for i in s.items)
    if counter.count(joined) <= body_budget:
        return f"{header}\n{joined}"

    suffix_tokens = counter.count(_TRUNCATED_SUFFIX)
    text_budget = max(1, body_budget - suffix_tokens)
    truncated = counter.truncate_to_budget(joined, budget=text_budget, direction=s.truncate)
    return f"{header}\n{truncated}{_TRUNCATED_SUFFIX}"


async def _render_optional_section(
    s: Section, *, counter: TokenCounter, budget: int, query: Optional[str]
) -> tuple[int, str]:
    """Render an OPTIONAL section. Drop items tail-first until the body fits.

    Returns ``(tokens_used, rendered_string)``. If zero items fit, returns
    ``(0, "")``.
    """
    if not s.items:
        return 0, ""

    # Coerce to ContextItem list for consistent handling.
    raw = list(s.items)
    items: List[ContextItem] = [
        i if isinstance(i, ContextItem) else ContextItem(text=str(i), embed_key=f"{s.name}:{idx}")
        for idx, i in enumerate(raw)
    ]

    # Apply retriever if we have one and a query.
    if s.retriever is not None and query is not None:
        try:
            items = await s.retriever.rank(items, query)
        except Exception as e:
            log.warning(
                "Retriever failed for section %s (%s); using original order.", s.name, e
            )

    header = f"{s.name}:"
    header_tokens = counter.count(header + "\n")
    if header_tokens >= budget:
        return 0, ""

    kept_lines: List[str] = []
    used = header_tokens
    for item in items:
        t = counter.count(item.text) + 1  # +1 for newline join
        if used + t > budget:
            # Try truncating this single item to what's left.
            room = budget - used - 1
            if room <= 0:
                break
            suffix_tokens = counter.count(_TRUNCATED_SUFFIX)
            text_budget = max(1, room - suffix_tokens)
            truncated = counter.truncate_to_budget(
                item.text, budget=text_budget, direction=s.truncate,
            )
            if truncated:
                kept_lines.append(truncated + _TRUNCATED_SUFFIX)
                used = budget  # exhausted
            break
        kept_lines.append(item.text)
        used += t

    if not kept_lines:
        return 0, ""

    body = "\n".join(kept_lines)
    return used, f"{header}\n{body}"


def _item_text(item: Union[str, ContextItem]) -> str:
    """Extract the text string from a str or ContextItem."""
    return item.text if isinstance(item, ContextItem) else item
