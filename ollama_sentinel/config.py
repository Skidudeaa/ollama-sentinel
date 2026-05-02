"""
Configuration management for Ollama Sentinel.
"""
import logging
import pathlib
from typing import Optional

import yaml

from .models import SentinelConfig
from .triage.runner import TRIAGE_SYSTEM_PROMPT

log = logging.getLogger("ollama-sentinel")


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


def create_default_config(directory: str, output_dir: str = ".ollama_reviews") -> dict:
    """
    Create a default configuration dictionary.
    
    Args:
        directory: Directory to watch
        output_dir: Directory to store reviews
        
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
                    "name": "gemma3:4b",
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
                    "name": "gemma3:4b",
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