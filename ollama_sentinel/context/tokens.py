"""Token counting + budget-aware truncation.

Uses tiktoken (cl100k_base) as a universal approximator across Ollama models.
Falls back to a char-based estimator if tiktoken cannot load.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

log = logging.getLogger("ollama-sentinel")

_FALLBACK_CHARS_PER_TOKEN = 3.5


def _try_load_tiktoken():
    """Return a cl100k_base encoding, or None if tiktoken is unusable."""
    try:
        import tiktoken
        return tiktoken.get_encoding("cl100k_base")
    except Exception as e:  # pragma: no cover — hard to trigger in tests without mocking
        log.warning("tiktoken unavailable (%s); falling back to char-based estimator", e)
        return None


class TokenCounter:
    """Counts tokens and truncates strings to a token budget."""

    def __init__(self):
        self._enc = _try_load_tiktoken()

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is None:
            return int(len(text) / _FALLBACK_CHARS_PER_TOKEN)
        return len(self._enc.encode(text))

    def truncate_to_budget(
        self,
        text: str,
        *,
        budget: int,
        direction: Literal["head", "tail"] = "tail",
    ) -> str:
        """Return the longest prefix/suffix of text that fits within `budget` tokens."""
        if budget <= 0 or not text:
            return ""
        if self._enc is None:
            # Approximate via chars.
            char_budget = int(budget * _FALLBACK_CHARS_PER_TOKEN)
            if len(text) <= char_budget:
                return text
            return text[:char_budget] if direction == "tail" else text[-char_budget:]

        tokens = self._enc.encode(text)
        if len(tokens) <= budget:
            return text
        kept = tokens[:budget] if direction == "tail" else tokens[-budget:]
        return self._enc.decode(kept)
