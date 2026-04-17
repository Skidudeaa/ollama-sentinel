"""Triage subsystem: terminal-output diagnosis via local Ollama."""
from ollama_sentinel.triage.extractor import Reference, extract_references

__all__ = ["Reference", "extract_references"]
# run_triage and TRIAGE_SYSTEM_PROMPT are appended to __all__ in Task 4.
