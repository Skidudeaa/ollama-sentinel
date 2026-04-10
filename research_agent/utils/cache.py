# research_agent/utils/cache.py
from __future__ import annotations
import json
import os
from typing import Any, Optional, Dict
import diskcache

class Cache:
    """Persistent caching implementation for reducing API calls.

    Values are serialized as JSON strings to avoid pickle deserialization
    attacks when caching content fetched from untrusted web sources.
    """

    def __init__(self, cache_dir: str = "./.cache", ttl_hours: int = 336):  # 2 weeks default
        """Initialize the cache."""
        self.cache_dir = os.path.expanduser(cache_dir)
        self.ttl = ttl_hours * 3600  # Convert to seconds

        # Create cache directory if it doesn't exist
        os.makedirs(self.cache_dir, exist_ok=True)

        # Initialize disk cache
        self.cache = diskcache.Cache(self.cache_dir)

    @staticmethod
    def _serialize(value: Any) -> str:
        """Serialize value to JSON string for safe storage."""
        if hasattr(value, "__dataclass_fields__"):
            from dataclasses import asdict
            return json.dumps(asdict(value))
        if hasattr(value, "model_dump"):
            return json.dumps(value.model_dump())
        return json.dumps(value)

    def get(self, key: str) -> Any:
        """Get value from cache. Returns the deserialized JSON dict or None."""
        raw = self.cache.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in cache as JSON."""
        ttl = ttl or self.ttl
        try:
            self.cache.set(key, self._serialize(value), expire=ttl)
            return True
        except Exception:
            return False
    
    def delete(self, key: str) -> bool:
        """Delete item from cache."""
        try:
            self.cache.delete(key)
            return True
        except Exception:
            return False
    
    def clear(self) -> bool:
        """Clear all items from cache."""
        try:
            self.cache.clear()
            return True
        except Exception:
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "size": self.cache.size,
            "directory": self.cache_dir,
            "ttl_hours": self.ttl / 3600,
            "item_count": len(self.cache)
        }