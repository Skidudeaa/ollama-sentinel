"""Shared test fixtures for ollama-sentinel tests."""
import pathlib

import pytest
import yaml

from ollama_sentinel.models import (
    OllamaConfig,
    OllamaModelConfig,
    OutputConfig,
    ProcessingConfig,
    SentinelConfig,
    WatchConfig,
)


@pytest.fixture
def default_model_config():
    """A minimal valid OllamaModelConfig."""
    return OllamaModelConfig(name="test-model", system_prompt="Review this code.")


@pytest.fixture
def valid_ollama_config(default_model_config):
    """A minimal valid OllamaConfig with a default model."""
    return OllamaConfig(
        host="http://localhost:11434",
        models={"default": default_model_config},
    )


@pytest.fixture
def valid_sentinel_config(tmp_path, valid_ollama_config):
    """A minimal valid SentinelConfig rooted at tmp_path."""
    return SentinelConfig(
        watch=WatchConfig(directory=str(tmp_path)),
        ollama=valid_ollama_config,
        processing=ProcessingConfig(),
        output=OutputConfig(),
    )


@pytest.fixture
def config_yaml_path(tmp_path, valid_sentinel_config):
    """Write a valid YAML config file and return its path."""
    config_dict = {
        "watch": {
            "directory": str(tmp_path),
            "recursive": True,
            "ignore_patterns": ["*.log", "**/__pycache__/**"],
            "debounce_ms": 1500,
        },
        "ollama": {
            "host": "http://localhost:11434",
            "models": {
                "default": {
                    "name": "test-model",
                    "system_prompt": "Review this code.",
                }
            },
            "request_timeout": 120,
        },
        "processing": {
            "max_chars_per_chunk": 12000,
            "overlap_chars": 500,
            "max_concurrent_reviews": 3,
            "max_concurrent_chunks_per_file": 2,
            "git_diff_mode": False,
        },
        "output": {
            "directory": ".ollama_reviews",
            "format": "markdown",
            "console_output": False,
            "compress": False,
            "diff_based_history": False,
            "history": {"enabled": True, "max_versions": 5},
        },
    }
    config_file = tmp_path / "ollama-sentinel.yaml"
    config_file.write_text(yaml.dump(config_dict, sort_keys=False))
    return config_file
