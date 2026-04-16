# research_agent/core/config.py
from __future__ import annotations
import os
import toml
from pathlib import Path
from typing import Any, Dict, Optional, Union

from research_agent.core.logging import get_logger

logger = get_logger(__name__)

class Config:
    """Enhanced configuration management with environment overrides and validation."""
    
    _instance = None  # Singleton instance

    def __new__(cls, config_path: Optional[Union[str, Path]] = None, *args, **kwargs):
        """Implement as singleton for global access."""
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance. Use in tests to avoid state leaking between test cases."""
        cls._instance = None
    
    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        """Initialize configuration.
        
        Args:
            config_path: Path to TOML configuration file
        """
        if self._initialized:
            return
            
        # Default configuration
        self._config = {
            "api": {
                "openai_model": "gpt-4o-preview",
                "local_embedding_model": "sentence-transformers/all-mpnet-base-v2",
                "use_local_embeddings": True,
                "synthesis_context_tokens": 12000,
            },
            "embedding": {
                "enabled": False,
                "host": "http://localhost:11434",
                "model": "nomic-embed-text",
            },
            "search": {
                "primary_engine": "auto",
                "results_per_query": 10,
                "enable_fallbacks": True,
                "max_search_iterations": 3,
                "synthesis_sources": 5
            },
            "memory": {
                "db_path": "./.weaviate",
                "cache_path": "./.cache",
                "cache_ttl_hours": 336
            },
            "browser": {
                "headless": True,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "extraction_methods": ["trafilatura", "playwright", "justext"],
                "max_content_per_page": 20000,
                "enable_javascript": True,
                "page_load_timeout": 30
            },
            "agent": {
                "confidence_threshold": 0.7,
                "enable_self_critique": True,
                "synthesis_temperature": 0.1,
                "verification_temperature": 0.0,
                "max_iterations": 3
            },
            "cli": {
                "history_file": "~/.research_history",
                "max_history_items": 100,
                "enable_rich_formatting": True,
                "show_source_snippets": True,
                "save_results": True,
                "results_dir": "~/research_results"
            }
        }
        
        # Load from file if specified
        if config_path:
            path = Path(config_path)
            if path.exists():
                try:
                    file_config = toml.load(path)
                    self._deep_update(self._config, file_config)
                    logger.info(f"Loaded configuration from {path}")
                except Exception as e:
                    logger.error(f"Error loading configuration from {path}: {e}")
        
        # Override with environment variables
        self._load_env_overrides()
        
        # Expand paths
        self._expand_paths()
        
        self._initialized = True
    
    def _deep_update(self, target: Dict, source: Dict):
        """Update nested dictionary recursively."""
        for k, v in source.items():
            if isinstance(v, dict) and k in target and isinstance(target[k], dict):
                self._deep_update(target[k], v)
            else:
                target[k] = v
    
    def _load_env_overrides(self):
        """Load configuration overrides from environment variables."""
        # Core API keys
        if os.environ.get("OPENAI_API_KEY"):
            self._config["api"]["openai_api_key"] = os.environ.get("OPENAI_API_KEY")
            
        if os.environ.get("SERPAPI_API_KEY"):
            self._config["api"]["serpapi_api_key"] = os.environ.get("SERPAPI_API_KEY")
            
        # Other overrides
        if os.environ.get("RESEARCH_MODEL"):
            self._config["api"]["openai_model"] = os.environ.get("RESEARCH_MODEL")
            
        if os.environ.get("RESEARCH_USE_LOCAL_EMBEDDINGS"):
            self._config["api"]["use_local_embeddings"] = os.environ.get("RESEARCH_USE_LOCAL_EMBEDDINGS").lower() == "true"
            
        if os.environ.get("RESEARCH_CACHE_PATH"):
            self._config["memory"]["cache_path"] = os.environ.get("RESEARCH_CACHE_PATH")
            
        if os.environ.get("RESEARCH_DB_PATH"):
            self._config["memory"]["db_path"] = os.environ.get("RESEARCH_DB_PATH")
    
    def _expand_paths(self):
        """Expand all paths in configuration."""
        # Memory paths
        self._config["memory"]["db_path"] = os.path.expanduser(self._config["memory"]["db_path"])
        self._config["memory"]["cache_path"] = os.path.expanduser(self._config["memory"]["cache_path"])
        
        # CLI paths
        self._config["cli"]["history_file"] = os.path.expanduser(self._config["cli"]["history_file"])
        self._config["cli"]["results_dir"] = os.path.expanduser(self._config["cli"]["results_dir"])
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """Get configuration value using dot notation.
        
        Args:
            key_path: Dot-separated path to configuration value (e.g., "api.openai_model")
            default: Default value if key not found
            
        Returns:
            Configuration value
        """
        parts = key_path.split(".")
        value = self._config
        
        for part in parts:
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
            
        return value
    
    def set(self, key_path: str, value: Any):
        """Set configuration value using dot notation.
        
        Args:
            key_path: Dot-separated path to configuration value
            value: Value to set
        """
        parts = key_path.split(".")
        target = self._config
        
        # Navigate to the final container
        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
            
        # Set the value
        target[parts[-1]] = value
    
    def save(self, path: Optional[Union[str, Path]] = None):
        """Save configuration to file.
        
        Args:
            path: Path to save configuration file
        """
        if not path:
            path = "config.toml"
            
        path = os.path.expanduser(path)
        
        try:
            with open(path, "w") as f:
                toml.dump(self._config, f)
            logger.info(f"Saved configuration to {path}")
        except Exception as e:
            logger.error(f"Error saving configuration to {path}: {e}")
    
    def as_dict(self) -> Dict:
        """Get full configuration as dictionary."""
        return self._config.copy()