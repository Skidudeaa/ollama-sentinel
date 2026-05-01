"""
Data models for Ollama Sentinel configuration.
"""
import logging
from enum import Enum
from typing import Dict, List, Optional

from urllib.parse import urlparse

from pydantic import BaseModel, field_validator, model_validator

log = logging.getLogger("ollama-sentinel")

_PROCESSING_DEPRECATION_LOGGED = False


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
    """Configuration for the Ollama embedding backend."""
    enabled: bool = True
    model: str = "nomic-embed-text"


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