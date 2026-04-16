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
    "chunk_by_lines",
]
