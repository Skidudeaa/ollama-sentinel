"""Triage subsystem: terminal-output diagnosis via local Ollama."""
from ollama_sentinel.triage.extractor import Reference, extract_references
from ollama_sentinel.triage.runner import TRIAGE_SYSTEM_PROMPT, run_triage

__all__ = [
    "Reference",
    "TRIAGE_SYSTEM_PROMPT",
    "extract_references",
    "run_triage",
]
