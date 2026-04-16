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
