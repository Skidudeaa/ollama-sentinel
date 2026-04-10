# research_agent/cli/history.py
from __future__ import annotations
import os
import json
import time
from typing import List, Dict, Any, Optional

class HistoryManager:
    """Manages command history for the CLI."""
    
    def __init__(self, filepath: str = "~/.research_history", max_items: int = 100):
        """Initialize history manager.
        
        Args:
            filepath: Path to history file
            max_items: Maximum history items
        """
        self.filepath = os.path.expanduser(filepath)
        self.max_items = max_items
        self.items: List[Dict[str, Any]] = []
        
        # Load history file
        self._load()
    
    def _load(self):
        """Load history from file."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    self.items = json.load(f)
            except Exception:
                # If file is corrupted, start fresh
                self.items = []
        else:
            # Create parent directory if it doesn't exist
            os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
    
    def _save(self):
        """Save history to file."""
        try:
            with open(self.filepath, "w") as f:
                json.dump(self.items, f, indent=2)
        except Exception:
            # Ignore errors
            pass
    
    def add_item(self, item: Dict[str, Any]):
        """Add item to history.
        
        Args:
            item: History item
        """
        # Don't add duplicate queries
        if self.items and "query" in item and any(i.get("query") == item["query"] for i in self.items[-5:]):
            # Update existing item timestamp instead
            for i in self.items:
                if i.get("query") == item["query"]:
                    i["time"] = item.get("time", time.strftime("%Y-%m-%d %H:%M"))
                    break
        else:
            # Add new item
            self.items.append(item)
            
            # Trim if needed
            while len(self.items) > self.max_items:
                self.items.pop(0)
                
        # Save to file
        self._save()
		
		# research_agent/cli/history.py (continued)
    def get_items(self) -> List[Dict[str, Any]]:
        """Get all history items.
        
        Returns:
            List of history items
        """
        return self.items
    
    def clear(self):
        """Clear history."""
        self.items = []
        self._save()
    
    def get_item(self, index: int) -> Optional[Dict[str, Any]]:
        """Get item by index.
        
        Args:
            index: Item index
            
        Returns:
            History item or None if index is invalid
        """
        if 0 <= index < len(self.items):
            return self.items[index]
        return None