"""
Ollama Sentinel - Continuous code review with Ollama.
"""

__version__ = "0.1.0"

from .config import load_config
from .models import SentinelConfig
from .watcher import FileSentinel

__all__ = ["load_config", "SentinelConfig", "FileSentinel"]