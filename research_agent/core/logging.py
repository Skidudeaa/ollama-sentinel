# research_agent/core/logging.py
from __future__ import annotations
import os
import logging
import sys
from typing import Optional, Dict

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Store logger instances
_loggers: Dict[str, logging.Logger] = {}

def get_logger(name: str, level: Optional[int] = None) -> logging.Logger:
    """Get or create a logger with the given name.
    
    Args:
        name: Logger name
        level: Optional logging level
        
    Returns:
        Logger instance
    """
    if name in _loggers:
        return _loggers[name]
        
    # Create new logger
    logger = logging.getLogger(name)
    
    # Set level from environment or parameter
    if level is None:
        env_level = os.environ.get("RESEARCH_LOG_LEVEL", "INFO").upper()
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        level = level_map.get(env_level, logging.INFO)
        
    logger.setLevel(level)
    
    # Store for reuse
    _loggers[name] = logger
    return logger