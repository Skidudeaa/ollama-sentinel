"""Shared context-assembly primitives for ollama-sentinel and research_agent."""
from ollama_sentinel.context.assembler import (
    ContextItem,
    Priority,
    Retriever,
    Section,
    assemble,
    chunk_by_lines,
)
from ollama_sentinel.context.embeddings import EmbeddingUnavailable, OllamaEmbedder
from ollama_sentinel.context.recipes import build_research_context, build_review_context
from ollama_sentinel.context.retrievers import NullRetriever, SemanticRetriever
from ollama_sentinel.context.tokens import TokenCounter

__all__ = [
    "ContextItem",
    "EmbeddingUnavailable",
    "NullRetriever",
    "OllamaEmbedder",
    "Priority",
    "Retriever",
    "Section",
    "SemanticRetriever",
    "TokenCounter",
    "assemble",
    "build_research_context",
    "build_review_context",
    "chunk_by_lines",
]
