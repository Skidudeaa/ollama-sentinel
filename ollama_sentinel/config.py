"""
Configuration management for Ollama Sentinel.
"""
import logging
import pathlib
from typing import List, Optional, Sequence

import httpx
import yaml

from .models import SentinelConfig
from .triage.runner import TRIAGE_SYSTEM_PROMPT

log = logging.getLogger("ollama-sentinel")

# The reviewer model written into a fresh config when no installed model can be
# detected (Ollama unreachable, or only embedding models present). It is a
# small, broadly-available default; the user can `ollama pull` it or edit it.
DEFAULT_REVIEWER_MODEL = "gemma3:4b"

# Code-review quality preferences, applied only to break ties among installed
# chat models. Coder models first; among the rest, known general-purpose
# families before unknowns; clinical/specialist models last.
_PREFERRED_FAMILIES = (
    "qwen",
    "llama",
    "gemma",
    "mistral",
    "devstral",
    "deepseek",
    "codestral",
    "phi",
)
# Specific fragments (not bare "med", which would match "medium") that mark a
# domain-specialist model unsuited to general code review.
_DISFAVORED_KEYWORDS = (
    "meditron",
    "medllama",
    "medgemma",
    "biomistral",
    "clinical",
)


def list_installed_models(
    host: str = "http://localhost:11434", timeout: float = 5.0
) -> List[str]:
    """Return the names of models installed in Ollama via ``GET /api/tags``.

    Best-effort: returns an empty list on any failure (Ollama unreachable,
    timeout, non-200, malformed payload) so callers never break.
    """
    url = host.rstrip("/") + "/api/tags"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
        return [m["name"] for m in data.get("models", []) if m.get("name")]
    except Exception as e:  # noqa: BLE001 — detection must never break init
        log.debug("Could not list installed Ollama models from %s: %s", url, e)
        return []


def select_reviewer_model(
    installed: Sequence[str], fallback: str = DEFAULT_REVIEWER_MODEL
) -> str:
    """Pick the best reviewer model from ``installed`` names.

    Heuristic (local-first): exclude embedding models, then prefer local over
    cloud, coder models over general, general-purpose families over unknowns,
    and clinical/specialist models last. Returns ``fallback`` when no
    chat-capable model is installed.
    """
    candidates = [name for name in installed if "embed" not in name.lower()]
    if not candidates:
        return fallback

    def rank(item):
        idx, name = item
        n = name.lower()
        is_cloud = "cloud" in n
        is_coder = "coder" in n or "-code" in n or n.startswith("code")
        is_disfavored = any(k in n for k in _DISFAVORED_KEYWORDS)
        is_preferred = any(k in n for k in _PREFERRED_FAMILIES)
        return (
            1 if is_cloud else 0,       # local before cloud (local-first)
            0 if is_coder else 1,       # coder models preferred for review
            1 if is_disfavored else 0,  # clinical/specialist models last
            0 if is_preferred else 1,   # known families before unknowns
            idx,                        # stable: preserve /api/tags order
        )

    return min(enumerate(candidates), key=rank)[1]


def load_config(config_path: pathlib.Path) -> Optional[SentinelConfig]:
    """
    Load configuration from YAML file.
    
    Args:
        config_path: Path to the configuration file
        
    Returns:
        Validated configuration object or None if loading fails
    """
    try:
        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)
        
        # Validate configuration using Pydantic
        return SentinelConfig(**config_data)
    
    except FileNotFoundError:
        log.error(f"Configuration file not found: {config_path}")
    except yaml.YAMLError as e:
        log.error(f"Invalid YAML in configuration file: {e}")
    except Exception as e:
        log.error(f"Failed to load configuration: {e}")
    
    return None


def create_default_config(
    directory: str,
    output_dir: str = ".ollama_reviews",
    reviewer_model: str = DEFAULT_REVIEWER_MODEL,
) -> dict:
    """
    Create a default configuration dictionary.

    Args:
        directory: Directory to watch
        output_dir: Directory to store reviews
        reviewer_model: Ollama model name for the ``default`` and ``triage``
            roles (typically auto-detected from installed models)

    Returns:
        Default configuration dictionary
    """
    return {
        "watch": {
            "directory": directory,
            "recursive": True,
            # Built-in patterns (dotdirs, binaries, lock files) are always active.
            # List only project-specific extras here.
            "ignore_patterns": [
                "*.md",
                "*.log",
                f"**/{output_dir}/**",
            ],
            "debounce_ms": 1500,
            "max_file_size_kb": 512,
            "disable_builtin_ignores": False,
        },
        "ollama": {
            "host": "http://localhost:11434",
            "request_timeout": 180,
            "models": {
                "default": {
                    "name": reviewer_model,
                    "system_prompt": (
                        "You are a senior code reviewer. Return constructive, actionable feedback "
                        "for each file: identify bugs (with line numbers), design smells, and "
                        "small refactors that improve readability and performance. "
                        "Respond in GitHub-flavored markdown. "
                        "If the file is auto-generated or purely data, say 'No actionable feedback.'"
                    ),
                    "context_window": 8192,
                    "output_reserve_tokens": 2000,
                },
                "triage": {
                    "name": reviewer_model,
                    "system_prompt": TRIAGE_SYSTEM_PROMPT,
                    "context_window": 8192,
                    "output_reserve_tokens": 2000,
                }
            }
        },
        "processing": {
            "max_concurrent_reviews": 1,
            "max_concurrent_chunks_per_file": 1,
            "git_diff_mode": False,
        },
        "output": {
            "directory": output_dir,
            "format": "markdown",
            "console_output": True,
            "compress": False,
            "diff_based_history": False,
            "history": {
                "enabled": True,
                "max_versions": 5,
            }
        },
        "memory": {
            "enabled": True,
            "db_path": f"{output_dir}/memory.db",
            "neighbor_k": 10,
            "semantic_recall": True,
        },
        "embedding": {
            "enabled": True,
            "models": {
                "hot": "qwen3-embedding:4b",
                "consolidation": "qwen3-embedding:8b",
                "rerank": None,
            },
        },
    }