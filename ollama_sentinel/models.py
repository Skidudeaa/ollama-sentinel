"""
Data models for Ollama Sentinel configuration.
"""
import logging
from enum import Enum
from typing import Dict, List, Optional

from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

log = logging.getLogger("ollama-sentinel")

_PROCESSING_DEPRECATION_LOGGED = False

_EMBEDDING_DEPRECATION_LOGGED = False

# Single source of truth for non-hot embedding role defaults. Used by the
# field default, the legacy migrator, and the validator's merge step. If
# Phase B/C ever change these, update only here.
_NON_HOT_DEFAULTS = {
    "consolidation": "qwen3-embedding:8b",
    "rerank": None,
}


class ModelRole(str, Enum):
    """Roles for different Ollama models."""
    DEFAULT = "default"
    SECURITY = "security"
    PERFORMANCE = "performance"
    DOCUMENTATION = "documentation"


class OutputFormat(str, Enum):
    """Output formats for reviews."""
    MARKDOWN = "markdown"
    JSON = "json"
    HTML = "html"


class OllamaModelConfig(BaseModel):
    """Configuration for a specific Ollama model."""
    name: str
    system_prompt: str
    temperature: float = 0.1
    top_p: float = 0.9
    max_tokens: Optional[int] = None
    context_window: int = 8192
    output_reserve_tokens: int = 2000


class OllamaConfig(BaseModel):
    """Configuration for Ollama API."""
    host: str = "http://localhost:11434"
    models: Dict[str, OllamaModelConfig]
    request_timeout: int = 120

    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        """Validate the Ollama host URL has a safe scheme."""
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Ollama host must use http or https scheme, got '{parsed.scheme}'")
        if not parsed.hostname:
            raise ValueError("Ollama host must include a hostname")
        return v

    @field_validator("models")
    @classmethod
    def validate_models(cls, v):
        """Ensure there's at least a default model."""
        if "default" not in v:
            raise ValueError("A 'default' model must be specified")
        return v


class WatchConfig(BaseModel):
    """Configuration for directory watching."""
    directory: str
    recursive: bool = True
    ignore_patterns: List[str] = []
    debounce_ms: int = 1500
    max_file_size_kb: int = 512
    disable_builtin_ignores: bool = False


class ProcessingConfig(BaseModel):
    """Configuration for file processing."""
    max_concurrent_reviews: int = 3
    max_concurrent_chunks_per_file: int = 2
    git_diff_mode: bool = False
    # Legacy fields — kept as declared for back-compat; deprecated in favor of
    # OllamaModelConfig.context_window. Remove when no callers reference them.
    max_chars_per_chunk: int = 12000
    overlap_chars: int = 500

    @model_validator(mode="after")
    def _warn_legacy_fields(self):
        global _PROCESSING_DEPRECATION_LOGGED
        # A deprecation warning fires when non-default values are set for these fields.
        if (self.max_chars_per_chunk != 12000 or self.overlap_chars != 500):
            if not _PROCESSING_DEPRECATION_LOGGED:
                log.warning(
                    "ProcessingConfig.max_chars_per_chunk and overlap_chars are "
                    "deprecated; chunk sizing now derives from "
                    "OllamaModelConfig.context_window. Remove these fields "
                    "from your YAML to silence this warning."
                )
                _PROCESSING_DEPRECATION_LOGGED = True
        return self


class HistoryConfig(BaseModel):
    """Configuration for review history."""
    enabled: bool = True
    max_versions: int = 5


class OutputConfig(BaseModel):
    """Configuration for review output."""
    directory: str = ".ollama_reviews"
    format: OutputFormat = OutputFormat.MARKDOWN
    console_output: bool = True
    compress: bool = False
    diff_based_history: bool = False
    history: HistoryConfig = HistoryConfig()

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, v: str) -> str:
        """Ensure output directory is a safe relative path."""
        from pathlib import PurePosixPath
        parts = PurePosixPath(v).parts
        if ".." in parts:
            raise ValueError("Output directory must not contain '..' components")
        if PurePosixPath(v).is_absolute():
            raise ValueError("Output directory must be a relative path")
        return v


class NotificationsConfig(BaseModel):
    """Configuration for notifications."""
    enabled: bool = False
    url: Optional[str] = None


class EmbeddingConfig(BaseModel):
    """Configuration for the Ollama embedding backend.

    `models` is a name->model-id map. The `hot` role is required and is
    used on every file save. `consolidation` and `rerank` are pre-registered
    in the schema but UNWIRED today — they exist so Phases B and C don't
    need a second config migration. `rerank` defaults to None because the
    canonical reranker model is not yet chosen.

    Pre-registration is a property of the *schema*: a user YAML supplying
    only `hot` still gets the other two roles populated from defaults.

    The legacy flat-`model` field auto-migrates with a one-shot deprecation
    warning. The legacy field WILL HARD-ERROR in v0.3 — fix configs now.
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    models: Dict[str, Optional[str]] = {
        "hot": "qwen3-embedding:4b",
        **_NON_HOT_DEFAULTS,
    }

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_model_field(cls, data):
        if not isinstance(data, dict):
            return data
        has_legacy = "model" in data
        has_new = "models" in data
        if has_legacy and has_new:
            raise ValueError(
                "embedding.model and embedding.models are mutually exclusive; "
                "remove the legacy 'model' field."
            )
        if has_legacy:
            global _EMBEDDING_DEPRECATION_LOGGED
            if not _EMBEDDING_DEPRECATION_LOGGED:
                log.warning(
                    "embedding.model is deprecated and will hard-error in v0.3; "
                    "auto-migrating to embedding.models.hot for now."
                )
                _EMBEDDING_DEPRECATION_LOGGED = True
            migrated = {"hot": data["model"], **_NON_HOT_DEFAULTS}
            data = {k: v for k, v in data.items() if k != "model"}
            data["models"] = migrated
        return data

    @field_validator("models")
    @classmethod
    def _validate_models(cls, v: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
        if "hot" not in v:
            raise ValueError("embedding.models must include a 'hot' role")
        hot = v["hot"]
        if not isinstance(hot, str) or not hot.strip():
            raise ValueError("embedding.models['hot'] must be a non-empty string")
        for role, name in v.items():
            if name is None:
                continue
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f"embedding.models[{role!r}] must be a non-empty string or None"
                )
        # Spec deviation §1: pre-registration is a schema property. Merge
        # the defaults in so user-supplied keys win but missing keys are
        # filled. Without this, a partial dict like {"hot": "x"} leaves
        # consolidation/rerank absent and future B/C consumers see KeyError
        # where they expect a model name.
        return {**_NON_HOT_DEFAULTS, **v}


class MemoryConfig(BaseModel):
    """Configuration for violation memory."""
    enabled: bool = True
    db_path: str = ".ollama_reviews/memory.db"
    neighbor_k: int = 10
    semantic_recall: bool = True
    # Augment recall with 1-hop import-graph neighbors so a finding on a
    # frequently-imported util surfaces when editing its callers (and vice
    # versa). Python-only; degrades silently to single-file recall otherwise.
    structural_recall: bool = True


class SentinelConfig(BaseModel):
    """Main application configuration."""
    watch: WatchConfig
    ollama: OllamaConfig
    processing: ProcessingConfig = ProcessingConfig()
    output: OutputConfig = OutputConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    memory: MemoryConfig = MemoryConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()