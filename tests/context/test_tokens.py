"""Tests for TokenCounter."""
from unittest.mock import patch

from ollama_sentinel.context.tokens import TokenCounter


class TestTokenCounter:
    def test_counts_via_tiktoken(self):
        counter = TokenCounter()
        # "hello world" is 2 tokens in cl100k_base.
        assert counter.count("hello world") == 2

    def test_count_empty_string_is_zero(self):
        assert TokenCounter().count("") == 0

    def test_fallback_estimator_when_tiktoken_unavailable(self):
        # Force the fallback path by simulating import failure.
        with patch("ollama_sentinel.context.tokens._try_load_tiktoken", return_value=None):
            counter = TokenCounter()
            # len("abcdefg") // 3.5 -> 2
            assert counter.count("abcdefg") == 2

    def test_truncate_to_budget_returns_prefix(self):
        counter = TokenCounter()
        text = "the quick brown fox jumps over the lazy dog"
        out = counter.truncate_to_budget(text, budget=3, direction="tail")
        assert counter.count(out) <= 3
        assert text.startswith(out)

    def test_truncate_head_returns_suffix(self):
        counter = TokenCounter()
        text = "the quick brown fox jumps over the lazy dog"
        out = counter.truncate_to_budget(text, budget=3, direction="head")
        assert counter.count(out) <= 3
        assert text.endswith(out)
