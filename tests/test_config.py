"""Tests for ollama_sentinel config loading and default generation."""
import pathlib

import httpx
import pytest
import yaml

from ollama_sentinel.config import (
    create_default_config,
    list_installed_models,
    load_config,
    select_reviewer_model,
)


class TestLoadConfig:
    """Tests for load_config()."""

    def test_valid_yaml_returns_sentinel_config(self, config_yaml_path):
        config = load_config(config_yaml_path)
        assert config is not None
        assert config.ollama.host == "http://localhost:11434"
        assert "default" in config.ollama.models

    def test_missing_file_returns_none(self, tmp_path):
        result = load_config(tmp_path / "nonexistent.yaml")
        assert result is None

    def test_invalid_yaml_returns_none(self, tmp_path):
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("{{invalid: yaml: [")
        result = load_config(bad_yaml)
        assert result is None

    def test_yaml_missing_required_fields_returns_none(self, tmp_path):
        incomplete = tmp_path / "incomplete.yaml"
        incomplete.write_text(yaml.dump({"watch": {"directory": "."}}))
        result = load_config(incomplete)
        assert result is None

    def test_yaml_with_invalid_host_scheme_returns_none(self, tmp_path):
        config_dict = {
            "watch": {"directory": str(tmp_path)},
            "ollama": {
                "host": "ftp://evil.com",
                "models": {"default": {"name": "m", "system_prompt": "p"}},
            },
        }
        bad_host = tmp_path / "bad_host.yaml"
        bad_host.write_text(yaml.dump(config_dict))
        result = load_config(bad_host)
        assert result is None


class TestCreateDefaultConfig:
    """Tests for create_default_config()."""

    def test_returns_dict_with_all_sections(self):
        config = create_default_config(".", ".reviews")
        assert "watch" in config
        assert "ollama" in config
        assert "processing" in config
        assert "output" in config

    def test_watch_directory_matches_argument(self):
        config = create_default_config("/some/path")
        assert config["watch"]["directory"] == "/some/path"

    def test_output_directory_matches_argument(self):
        config = create_default_config(".", "custom_reviews")
        assert config["output"]["directory"] == "custom_reviews"

    def test_default_model_is_gemma3(self):
        config = create_default_config(".")
        assert config["ollama"]["models"]["default"]["name"] == "gemma3:4b"

    def test_reviewer_model_param_overrides_default_and_triage(self):
        config = create_default_config(".", reviewer_model="qwen3-coder:30b")
        models = config["ollama"]["models"]
        assert models["default"]["name"] == "qwen3-coder:30b"
        assert models["triage"]["name"] == "qwen3-coder:30b"

    def test_default_has_required_model_key(self):
        config = create_default_config(".")
        assert "default" in config["ollama"]["models"]

    def test_ignore_patterns_include_output_dir(self):
        config = create_default_config(".", "my_reviews")
        patterns = config["watch"]["ignore_patterns"]
        assert any("my_reviews" in p for p in patterns)

    def test_emits_memory_section(self):
        config = create_default_config(".", "my_reviews")
        assert "memory" in config
        assert config["memory"]["semantic_recall"] is True
        assert config["memory"]["neighbor_k"] == 10
        assert "my_reviews" in config["memory"]["db_path"]

    def test_emits_embedding_section(self):
        config = create_default_config(".")
        assert "embedding" in config
        assert config["embedding"]["enabled"] is True
        assert config["embedding"]["models"]["hot"] == "qwen3-embedding:4b"
        assert config["embedding"]["models"]["consolidation"] == "qwen3-embedding:8b"
        assert config["embedding"]["models"]["rerank"] is None

    def test_legacy_yaml_model_migrates_on_load(self, tmp_path):
        """A user YAML with the legacy embedding.model field loads and lifts to
        models.hot, with consolidation/rerank filled from schema defaults."""
        import yaml as _yaml
        import ollama_sentinel.models as models_module
        models_module._EMBEDDING_DEPRECATION_LOGGED = False
        config_dict = {
            "watch": {"directory": str(tmp_path)},
            "ollama": {
                "host": "http://localhost:11434",
                "models": {"default": {"name": "m", "system_prompt": "p"}},
            },
            "embedding": {
                "enabled": True,
                "model": "legacy-embed-name",
            },
        }
        cfg_path = tmp_path / "ollama-sentinel.yaml"
        cfg_path.write_text(_yaml.dump(config_dict))
        cfg = load_config(cfg_path)
        assert cfg is not None
        assert cfg.embedding.models["hot"] == "legacy-embed-name"
        assert cfg.embedding.models["consolidation"] == "qwen3-embedding:8b"
        assert cfg.embedding.models["rerank"] is None

    def test_default_model_has_context_window(self):
        config = create_default_config(".")
        m = config["ollama"]["models"]["default"]
        assert m["context_window"] == 8192
        assert m["output_reserve_tokens"] == 2000

    def test_processing_drops_deprecated_char_fields(self):
        config = create_default_config(".")
        assert "max_chars_per_chunk" not in config["processing"]
        assert "overlap_chars" not in config["processing"]

    def test_emits_triage_model_role(self):
        config = create_default_config(".")
        models = config["ollama"]["models"]
        assert "triage" in models
        triage = models["triage"]
        assert "system_prompt" in triage
        assert "DIAGNOSIS" in triage["system_prompt"]
        assert triage["context_window"] == 8192
        assert triage["output_reserve_tokens"] == 2000


class TestSelectReviewerModel:
    """Tests for select_reviewer_model() — pure model-selection heuristic."""

    def test_empty_list_returns_fallback(self):
        assert select_reviewer_model([]) == "gemma3:4b"

    def test_custom_fallback_is_used_when_empty(self):
        assert select_reviewer_model([], fallback="llama3:8b") == "llama3:8b"

    def test_prefers_coder_model(self):
        installed = ["qwen3.6:35b", "qwen3-coder:30b", "gemma3:4b"]
        assert select_reviewer_model(installed) == "qwen3-coder:30b"

    def test_excludes_embedding_models(self):
        # An embedding model can't do chat review; never select it.
        installed = ["qwen3-embedding:4b", "qwen3.6:35b"]
        assert select_reviewer_model(installed) == "qwen3.6:35b"

    def test_only_embedding_models_returns_fallback(self):
        installed = ["qwen3-embedding:4b", "qwen3-embedding:8b"]
        assert select_reviewer_model(installed) == "gemma3:4b"

    def test_prefers_local_over_cloud(self):
        # Local-first: a local chat model beats a cloud coder model.
        installed = ["deepseek-v4-pro:cloud", "qwen3.6:35b"]
        assert select_reviewer_model(installed) == "qwen3.6:35b"

    def test_deprioritizes_medical_specialist_models(self):
        # A general model is a better default reviewer than a clinical model,
        # even though "medllama2" contains the preferred "llama" family token.
        installed = ["medllama2:latest", "meditron:latest", "qwen3.6:35b"]
        assert select_reviewer_model(installed) == "qwen3.6:35b"

    def test_real_world_zoo_picks_coder(self):
        installed = [
            "qwen3-coder:30b",
            "qwen3.6:35b",
            "qwen3-embedding:4b",
            "deepseek-v4-pro:cloud",
            "devstral-2:123b-cloud",
            "hf.co/MaziyarPanahi/BioMistral-Clinical-7B-GGUF:latest",
            "medgemma1.5:4b",
            "meditron:latest",
            "medllama2:latest",
        ]
        assert select_reviewer_model(installed) == "qwen3-coder:30b"


class TestListInstalledModels:
    """Tests for list_installed_models() — best-effort /api/tags query."""

    def test_returns_model_names_on_success(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:11434/api/tags",
            json={"models": [{"name": "qwen3-coder:30b"}, {"name": "qwen3.6:35b"}]},
        )
        assert list_installed_models("http://localhost:11434") == [
            "qwen3-coder:30b",
            "qwen3.6:35b",
        ]

    def test_returns_empty_list_on_http_error(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:11434/api/tags", status_code=404
        )
        assert list_installed_models("http://localhost:11434") == []

    def test_returns_empty_list_on_connection_error(self, httpx_mock):
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))
        assert list_installed_models("http://localhost:11434") == []
