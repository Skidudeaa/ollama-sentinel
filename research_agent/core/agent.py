# research_agent/core/agent.py
from __future__ import annotations
import os
import logging
import time
import json
from typing import Dict, Any, Optional, List, Union
from pathlib import Path

from research_agent.core.config import Config
from research_agent.core.workflow import build_workflow, AgentState # Import AgentState
from research_agent.core.models import ResearchSession
from research_agent.core.logging import get_logger

logger = get_logger(__name__)

class NullTelemetry:
    """Null telemetry implementation for when telemetry is disabled."""
    def span(self, name, attrs=None):
        class NullContext:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        return NullContext()

class ResearchAgent:
    """Main research agent class."""
    
    def __init__(
        self, 
        repo_path: Optional[str] = None,
        config_path: Optional[str] = None
    ):
        """Initialize the research agent.
        
        Args:
            repo_path: Path to code repository
            config_path: Path to configuration file
        """
        # Load configuration
        self.config = Config(config_path)
        
        # Set up repository path
        self.repo_path = repo_path or os.getcwd()
        
        # Get API keys
        self.openai_api_key = self.config.get("api.openai_api_key")
        self.serpapi_api_key = self.config.get("api.serpapi_api_key")
        
        if not self.openai_api_key:
            logger.warning("OpenAI API key not found. Set OPENAI_API_KEY environment variable.")
            
        if not self.serpapi_api_key:
            logger.warning("SERPAPI API key not found. Web search will use DuckDuckGo only.")
        
        # Set up telemetry (null implementation)
        self.telemetry = NullTelemetry()
        
        # Initialize workflow
        self.graph, self.components = build_workflow(
            config=self.config.as_dict(),
            repo_path=self.repo_path,
            openai_api_key=self.openai_api_key,
            serpapi_api_key=self.serpapi_api_key,
            telemetry=self.telemetry
        )
        
        logger.info(f"Research agent initialized with repo: {self.repo_path}")
    
    def research(
        self, 
        query: str, 
        code_context: Optional[str] = None
    ) -> ResearchSession:
        """Execute a research query.
        
        Args:
            query: Research query
            code_context: Optional code context
            
        Returns:
            ResearchSession with results
        """
        logger.info(f"Starting research for: {query}")
        
        # Create session
        session = ResearchSession(
            query=query,
            code_context=code_context
        )
        
        # Initialize state using AgentState
        initial_state: AgentState = {
            "session": session,
            "step": "analyze",
            "search_results": None,
            "content_items": None,
            "code_results": None,
            "answer": None,
            "confidence": None,
            "verification": None,
            "refined_queries": None,
            "final_answer": None,
        }
        
        # Execute workflow
        try:
            start_time = time.time()
            result = self.graph.invoke(initial_state) # Use the fully initialized state
            end_time = time.time()
            
            # Extract final answer
            if "final_answer" in result:
                session.answer = result["final_answer"]
                
            if "confidence" in result:
                session.confidence = result["confidence"]
                
            session.end_time = end_time
            
            logger.info(f"Research completed in {end_time - start_time:.2f}s with confidence {session.confidence:.2f}")
            
            # Save session if configured
            if self.config.get("cli.save_results", False):
                self._save_session(session)
                
            return session
            
        except Exception as e:
            logger.error(f"Error executing research: {e}")
            session.fail_step("workflow", str(e))
            session.end_time = time.time()
            return session
    
    def _save_session(self, session: ResearchSession):
        """Save session to results directory."""
        try:
            # Get results directory
            results_dir = self.config.get("cli.results_dir", "~/research_results")
            results_dir = os.path.expanduser(results_dir)
            
            # Create directory if it doesn't exist
            os.makedirs(results_dir, exist_ok=True)
            
            # Create filename from query
            filename = f"research_{int(session.start_time)}_{session.query[:30].replace(' ', '_')}.json"
            filepath = os.path.join(results_dir, filename)
            
            # Serialize session
            data = {
                "query": session.query,
                "code_context": session.code_context,
                "answer": session.answer,
                "confidence": session.confidence,
                "start_time": session.start_time,
                "end_time": session.end_time,
                "duration": session.duration,
                "steps": [
                    {
                        "name": step.name,
                        "status": step.status,
                        "start_time": step.start_time,
                        "end_time": step.end_time,
                        "error": step.error
                    }
                    for step in session.steps
                ]
            }
            
            # Save to file
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
                
            logger.info(f"Saved session to {filepath}")
            
        except Exception as e:
            logger.error(f"Error saving session: {e}")