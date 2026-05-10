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
        assert model.think is None
        assert model.max_tokens is None

    def test_custom_values(self):
        model = OllamaModelConfig(
            name="llama3",
            system_prompt="Security review.",
            temperature=0.5,
            top_p=0.8,
            think=False,
            max_tokens=4096,
        )
        assert model.temperature == 0.5
        assert model.top_p == 0.8
        assert model.think is False
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
        # See TestEmbeddingConfigMigration for full models-shape coverage.
        assert cfg.models["hot"] == "qwen3-embedding:4b"


class TestEmbeddingConfigMigration:
    """Phase A: EmbeddingConfig is a named-role dict with legacy migration."""

    def test_default_models_shape(self):
        cfg = EmbeddingConfig()
        assert set(cfg.models.keys()) == {"hot", "consolidation", "rerank"}
        assert cfg.models["hot"] == "qwen3-embedding:4b"
        assert cfg.models["consolidation"] == "qwen3-embedding:8b"
        assert cfg.models["rerank"] is None

    def test_partial_models_dict_fills_defaults(self):
        """Spec deviation §1: pre-registration is a schema property, not an
        input property. Supplying only `hot` must still populate the other
        two roles from defaults so future Phase B/C consumers don't see None
        where they expect a model name."""
        cfg = EmbeddingConfig(models={"hot": "my-custom-hot"})
        assert cfg.models["hot"] == "my-custom-hot"
        assert cfg.models["consolidation"] == "qwen3-embedding:8b"
        assert cfg.models["rerank"] is None

    def test_models_must_include_hot_role(self):
        with pytest.raises(ValidationError, match="hot"):
            EmbeddingConfig(models={"consolidation": "x"})

    def test_hot_role_must_be_non_empty_string(self):
        with pytest.raises(ValidationError, match="non-empty"):
            EmbeddingConfig(models={"hot": "  "})

    def test_other_roles_may_be_none(self):
        cfg = EmbeddingConfig(models={"hot": "h", "rerank": None})
        assert cfg.models["rerank"] is None

    def test_other_roles_must_be_non_empty_string_when_set(self):
        with pytest.raises(ValidationError, match="non-empty"):
            EmbeddingConfig(models={"hot": "h", "consolidation": "  "})

    def test_extra_top_level_field_is_forbidden(self):
        # Phase A locks the schema with extra="forbid" so typos in YAML
        # surface loudly rather than silently being ignored.
        with pytest.raises(ValidationError):
            EmbeddingConfig(enabled=True, models={"hot": "h"}, oops=True)

    def test_unknown_role_name_is_rejected(self):
        """Spec deviation §5: role names inside `models` are a closed set
        (hot, consolidation, rerank). Typos like `consolitation` would
        otherwise silently get added as a custom role while the merge-in-
        validator quietly fills the intended key with the default,
        delivering the wrong model to Phase B/C consumers. Reject typos
        loudly at config load time."""
        with pytest.raises(ValidationError, match="unrecognized role"):
            EmbeddingConfig(models={"hot": "h", "consolitation": "x"})

    def test_legacy_model_field_migrates_to_models_hot(self, caplog):
        import ollama_sentinel.models as models_module
        models_module._EMBEDDING_DEPRECATION_LOGGED = False
        with caplog.at_level("WARNING"):
            cfg = EmbeddingConfig(model="legacy-embed-name")
        assert cfg.models["hot"] == "legacy-embed-name"
        assert cfg.models["consolidation"] == "qwen3-embedding:8b"
        assert cfg.models["rerank"] is None
        assert "v0.3" in caplog.text or "0.3" in caplog.text
        assert "deprecated" in caplog.text.lower()

    def test_legacy_model_with_extra_field_still_rejects(self):
        """Spec deviation §1 corollary: legacy migrator strips the recognized
        `model` key; any unrecognized siblings still trigger extra='forbid'."""
        with pytest.raises(ValidationError):
            EmbeddingConfig(model="legacy-embed-name", oops="typo")

    def test_both_model_and_models_rejected(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            EmbeddingConfig(model="x", models={"hot": "y"})

    def test_timeout_seconds_default(self):
        cfg = EmbeddingConfig()
        assert cfg.timeout_seconds == 120

    def test_timeout_seconds_user_override(self):
        cfg = EmbeddingConfig(timeout_seconds=300)
        assert cfg.timeout_seconds == 300

    def test_timeout_seconds_must_be_positive(self):
        with pytest.raises(ValidationError, match="positive"):
            EmbeddingConfig(timeout_seconds=0)
        with pytest.raises(ValidationError, match="positive"):
            EmbeddingConfig(timeout_seconds=-5)

    def test_deprecation_warning_logs_only_once(self, caplog):
        """Spec deviation §2 + §4: mirror ProcessingConfig's one-shot guard
        so repeat config loads don't spam stderr. The first call must log;
        the second must not. The positive assertion on the first call is
        what makes this test fail correctly when the guard is missing — a
        negative-only assertion would pass vacuously when no warning fires
        at all."""
        import ollama_sentinel.models as models_module
        models_module._EMBEDDING_DEPRECATION_LOGGED = False
        with caplog.at_level("WARNING"):
            EmbeddingConfig(model="legacy-1")
        assert "deprecated" in caplog.text.lower()  # first call must log
        caplog.clear()
        with caplog.at_level("WARNING"):
            EmbeddingConfig(model="legacy-2")
        assert "deprecated" not in caplog.text.lower()  # second call must not log


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


class TestProcessingConfigGrounding:
    def test_grounding_defaults_to_true(self):
        cfg = ProcessingConfig()
        assert cfg.grounding is True

    def test_grounding_can_be_disabled(self):
        cfg = ProcessingConfig(grounding=False)
        assert cfg.grounding is False


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
