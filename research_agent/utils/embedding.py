# research_agent/utils/embedding.py
from __future__ import annotations
import os
import logging
from typing import Optional, Dict, Any, Union

from langchain_community.embeddings.openai import OpenAIEmbeddings
from langchain_huggingface import HuggingFaceEmbeddings

from research_agent.core.logging import get_logger

logger = get_logger(__name__)

# Cache for embedding models
_embedding_models: Dict[str, Any] = {}

def get_embedding_model(model_name: Optional[str] = None, openai_api_key: Optional[str] = None):
    """Get embedding model, using local model if specified or OpenAI otherwise.
    
    Args:
        model_name: Name of local embedding model (HuggingFace model ID)
        openai_api_key: OpenAI API key
        
    Returns:
        Embedding model
    """
    global _embedding_models
    
    # If model name is None, use OpenAI
    if model_name is None:
        # Get OpenAI model
        key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        cache_key = f"openai_{key[:5]}" if key else "openai_default"
        
        if cache_key in _embedding_models:
            return _embedding_models[cache_key]
            
        try:
            logger.info("Using OpenAI embeddings")
            model = OpenAIEmbeddings(openai_api_key=key)
            _embedding_models[cache_key] = model
            return model
        except Exception as e:
            logger.error(f"Error initializing OpenAI embeddings: {e}")
            raise
    
    # Use local model
    cache_key = f"local_{model_name}"
    
    if cache_key in _embedding_models:
        return _embedding_models[cache_key]
        
    try:
        logger.info(f"Using local embeddings: {model_name}")
        model = HuggingFaceEmbeddings(model_name=model_name)
        _embedding_models[cache_key] = model
        return model
    except Exception as e:
        logger.error(f"Error initializing local embeddings: {e}")
        
        # Fallback to OpenAI
        logger.info("Falling back to OpenAI embeddings")
        key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        model = OpenAIEmbeddings(openai_api_key=key)
        return model