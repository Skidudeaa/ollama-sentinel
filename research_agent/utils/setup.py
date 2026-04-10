# research_agent/utils/setup.py
from __future__ import annotations
import os
import sys
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from research_agent.core.logging import get_logger

logger = get_logger(__name__)

def setup_environment():
    """Set up the research agent environment."""
    logger.info("Setting up research agent environment")
    
    # Check Python version
    logger.info(f"Python version: {sys.version}")
    if sys.version_info < (3, 10):
        logger.error("Python 3.10 or higher is required")
        sys.exit(1)
    
    # Install Playwright
    try:
        logger.info("Installing Playwright browsers")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Error installing Playwright browsers: {e}")
        logger.info("You may need to install them manually with: python -m playwright install chromium")
    
    # Create default config if it doesn't exist
    config_path = Path("config.toml")
    if not config_path.exists():
        logger.info("Creating default configuration file")
        try:
            from research_agent.core.config import Config
            config = Config()
            config.save(config_path)
            logger.info(f"Default configuration created at {config_path}")
        except Exception as e:
            logger.error(f"Error creating configuration file: {e}")
    
    # Create cache and vector DB directories
    try:
        os.makedirs("./.cache", exist_ok=True)
        os.makedirs("./.weaviate", exist_ok=True)
        logger.info("Cache and database directories created")
    except Exception as e:
        logger.error(f"Error creating directories: {e}")
    
    # Check for API keys
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        logger.warning("OPENAI_API_KEY environment variable not set")
        logger.info("Set it with: export OPENAI_API_KEY=your-key-here")
    
    serpapi_key = os.environ.get("SERPAPI_API_KEY")
    if not serpapi_key:
        logger.warning("SERPAPI_API_KEY environment variable not set")
        logger.info("DuckDuckGo will be used for web search")
        logger.info("For better results, get a SerpAPI key and set it with: export SERPAPI_API_KEY=your-key-here")
    
    logger.info("Setup complete")