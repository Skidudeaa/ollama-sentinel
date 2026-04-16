"""Tests for ollama_sentinel.models configuration models."""

import pytest
from pydantic import ValidationError

from ollama_sentinel.models import (
    OllamaConfig,
    OllamaModelConfig,
    OutputConfig,
    SentinelConfig,
    WatchConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_model_entry():
    """A minimal valid OllamaModelConfig dict."""
    return {"name": "codellama", "system_prompt": "Review this code."}


@pytest.fixture
def valid_ollama_config_dict(valid_model_entry):
    """A minimal valid OllamaConfig dict with a 'default' model."""
    return {
        "host": "http://localhost:11434",
        "models": {"default": valid_model_entry},
    }


# ---------------------------------------------------------------------------
# OllamaConfig — model key validation
# ---------------------------------------------------------------------------


class TestOllamaConfigModels:
    def test_valid_with_default_model(self, valid_ollama_config_dict):
        config = OllamaConfig(**valid_ollama_config_dict)
        assert "default" in config.models
        assert config.models["default"].name == "codellama"

    def test_missing_default_model_raises(self, valid_model_entry):
        with pytest.raises(ValidationError, match="default"):
            OllamaConfig(
                models={"security": valid_model_entry},
            )

    def test_multiple_model_roles(self, valid_model_entry):
        config = OllamaConfig(
            models={
                "default": valid_model_entry,
                "security": {
                    "name": "llama3",
                    "system_prompt": "Focus on security.",
                },
                "performance": {
                    "name": "deepseek-coder",
                    "system_prompt": "Focus on performance.",
                    "temperature": 0.3,
                },
            },
        )
        assert len(config.models) == 3
        assert config.models["performance"].temperature == 0.3


# ---------------------------------------------------------------------------
# OllamaConfig — host validation
# ---------------------------------------------------------------------------


class TestOllamaConfigHost:
    def test_http_localhost(self, valid_model_entry):
        config = OllamaConfig(
            host="http://localhost:11434",
            models={"default": valid_model_entry},
        )
        assert config.host == "http://localhost:11434"

    def test_https_with_port(self, valid_model_entry):
        config = OllamaConfig(
            host="https://example.com:8080",
            models={"default": valid_model_entry},
        )
        assert config.host == "https://example.com:8080"

    def test_ftp_scheme_raises(self, valid_model_entry):
        with pytest.raises(ValidationError, match="http or https"):
            OllamaConfig(
                host="ftp://evil.com",
                models={"default": valid_model_entry},
            )

    def test_javascript_scheme_raises(self, valid_model_entry):
        with pytest.raises(ValidationError, match="http or https"):
            OllamaConfig(
                host="javascript:alert(1)",
                models={"default": valid_model_entry},
            )

    def test_empty_host_raises(self, valid_model_entry):
        with pytest.raises(ValidationError):
            OllamaConfig(
                host="",
                models={"default": valid_model_entry},
            )


# ---------------------------------------------------------------------------
# OllamaConfig — defaults
# ---------------------------------------------------------------------------


class TestOllamaConfigDefaults:
    def test_default_host(self, valid_model_entry):
        config = OllamaConfig(models={"default": valid_model_entry})
        assert config.host == "http://localhost:11434"

    def test_default_request_timeout(self, valid_model_entry):
        config = OllamaConfig(models={"default": valid_model_entry})
        assert config.request_timeout == 120


# ---------------------------------------------------------------------------
# OllamaModelConfig — defaults and optional fields
# ---------------------------------------------------------------------------


class TestOllamaModelConfig:
    def test_defaults(self):
        model = OllamaModelConfig(name="codellama", system_prompt="Review.")
        assert model.temperature == 0.1
        assert model.top_p == 0.9
        assert model.max_tokens is None

    def test_custom_values(self):
        model = OllamaModelConfig(
            name="llama3",
            system_prompt="Security review.",
            temperature=0.5,
            top_p=0.8,
            max_tokens=4096,
        )
        assert model.temperature == 0.5
        assert model.top_p == 0.8
        assert model.max_tokens == 4096


# ---------------------------------------------------------------------------
# OutputConfig — directory validation
# ---------------------------------------------------------------------------


class TestOutputConfig:
    def test_default_directory(self):
        config = OutputConfig()
        assert config.directory == ".ollama_reviews"

    def test_relative_subdirectory(self):
        config = OutputConfig(directory="reviews/sub")
        assert config.directory == "reviews/sub"

    def test_dotdot_traversal_raises(self):
        with pytest.raises(ValidationError, match="\\.\\."):
            OutputConfig(directory="../../etc/cron.d")

    def test_absolute_path_raises(self):
        with pytest.raises(ValidationError, match="relative path"):
            OutputConfig(directory="/etc/reviews")


# ---------------------------------------------------------------------------
# SentinelConfig — full and minimal construction
# ---------------------------------------------------------------------------


class TestSentinelConfig:
    def test_full_config(self, valid_model_entry):
        data = {
            "watch": {
                "directory": "./src",
                "recursive": True,
                "ignore_patterns": ["*.pyc", "__pycache__"],
                "debounce_ms": 2000,
            },
            "ollama": {
                "host": "http://localhost:11434",
                "models": {
                    "default": valid_model_entry,
                    "security": {
                        "name": "llama3",
                        "system_prompt": "Security audit.",
                    },
                },
                "request_timeout": 300,
            },
            "processing": {
                "max_chars_per_chunk": 8000,
                "overlap_chars": 200,
                "max_concurrent_reviews": 5,
                "max_concurrent_chunks_per_file": 3,
                "git_diff_mode": True,
            },
            "output": {
                "directory": "my_reviews",
                "format": "json",
                "console_output": False,
            },
            "notifications": {
                "enabled": True,
                "url": "https://hooks.slack.com/test",
            },
        }
        config = SentinelConfig(**data)
        assert config.watch.directory == "./src"
        assert config.watch.debounce_ms == 2000
        assert config.ollama.request_timeout == 300
        assert len(config.ollama.models) == 2
        assert config.processing.git_diff_mode is True
        assert config.output.directory == "my_reviews"
        assert config.notifications.enabled is True

    def test_minimal_config(self, valid_model_entry):
        data = {
            "watch": {"directory": "."},
            "ollama": {"models": {"default": valid_model_entry}},
        }
        config = SentinelConfig(**data)
        # Watch defaults
        assert config.watch.recursive is True
        assert config.watch.ignore_patterns == []
        assert config.watch.debounce_ms == 1500
        # Ollama defaults
        assert config.ollama.host == "http://localhost:11434"
        assert config.ollama.request_timeout == 120
        # Sub-config defaults
        assert config.processing.max_concurrent_reviews == 3
        assert config.output.directory == ".ollama_reviews"
        assert config.notifications.enabled is False


# ---------------------------------------------------------------------------
# ContextBuilder config additions (Task 10)
# ---------------------------------------------------------------------------

from ollama_sentinel.models import (  # noqa: E402
    EmbeddingConfig,
    MemoryConfig,
    ProcessingConfig,
)


class TestEmbeddingConfig:
    def test_defaults(self):
        cfg = EmbeddingConfig()
        assert cfg.enabled is True
        assert cfg.model == "nomic-embed-text"


class TestOllamaModelConfigTokenFields:
    def test_context_window_default(self):
        cfg = OllamaModelConfig(name="m", system_prompt="p")
        assert cfg.context_window == 8192
        assert cfg.output_reserve_tokens == 2000


class TestMemoryConfigSemanticFields:
    def test_neighbor_k_and_semantic_recall_defaults(self):
        cfg = MemoryConfig()
        assert cfg.neighbor_k == 10
        assert cfg.semantic_recall is True


class TestProcessingConfigDeprecation:
    def test_legacy_fields_are_accepted_and_warn_once(self, caplog):
        # Reset the module-level flag so this test works in isolation.
        import ollama_sentinel.models as models_module
        models_module._PROCESSING_DEPRECATION_LOGGED = False

        with caplog.at_level("WARNING"):
            ProcessingConfig(max_chars_per_chunk=99, overlap_chars=7)
        assert "deprecated" in caplog.text.lower() or "ignored" in caplog.text.lower()


class TestSentinelConfigEmbeddingField:
    def test_embedding_defaults_populate(self, tmp_path):
        cfg = SentinelConfig(
            watch=WatchConfig(directory=str(tmp_path)),
            ollama=OllamaConfig(
                host="http://localhost:11434",
                models={"default": OllamaModelConfig(name="m", system_prompt="p")},
            ),
        )
        assert isinstance(cfg.embedding, EmbeddingConfig)
        assert cfg.embedding.enabled is True
