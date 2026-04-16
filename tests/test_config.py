"""Tests for ollama_sentinel config loading and default generation."""
import pathlib

import pytest
import yaml

from ollama_sentinel.config import create_default_config, load_config


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
        assert config["embedding"]["model"] == "nomic-embed-text"
        assert config["embedding"]["enabled"] is True

    def test_default_model_has_context_window(self):
        config = create_default_config(".")
        m = config["ollama"]["models"]["default"]
        assert m["context_window"] == 8192
        assert m["output_reserve_tokens"] == 2000

    def test_processing_drops_deprecated_char_fields(self):
        config = create_default_config(".")
        assert "max_chars_per_chunk" not in config["processing"]
        assert "overlap_chars" not in config["processing"]
