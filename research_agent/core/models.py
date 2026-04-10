# research_agent/core/models.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import time

@dataclass
class ContentItem:
    """Represents content from a web page."""
    url: str
    title: str = ""
    content: str = ""
    html: str = ""
    source: str = "browser"
    archived: bool = False
    timestamp: float = field(default_factory=time.time)

@dataclass
class ResearchStep:
    """Represents a step in the research process."""
    name: str
    status: str = "pending"  # pending, running, completed, failed
    start_time: float = 0.0
    end_time: float = 0.0
    output: Any = None
    error: Optional[str] = None

@dataclass
class ResearchSession:
    """Represents a complete research session."""
    query: str
    code_context: Optional[str] = None
    steps: List[ResearchStep] = field(default_factory=list)
    sources: List[ContentItem] = field(default_factory=list)
    answer: str = ""
    confidence: float = 0.0
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    
    def add_step(self, name: str) -> ResearchStep:
        """Add a step to the session."""
        step = ResearchStep(name=name)
        self.steps.append(step)
        return step
    
    def get_step(self, name: str) -> Optional[ResearchStep]:
        """Get a step by name."""
        for step in self.steps:
            if step.name == name:
                return step
        return None
    
    def start_step(self, name: str) -> ResearchStep:
        """Start a step."""
        step = self.get_step(name)
        if not step:
            step = self.add_step(name)
        
        step.status = "running"
        step.start_time = time.time()
        return step
    
    def complete_step(self, name: str, output: Any = None) -> ResearchStep:
        """Complete a step."""
        step = self.get_step(name)
        if not step:
            return self.add_step(name)
        
        step.status = "completed"
        step.end_time = time.time()
        step.output = output
        return step
    
    def fail_step(self, name: str, error: str) -> ResearchStep:
        """Fail a step."""
        step = self.get_step(name)
        if not step:
            step = self.add_step(name)
        
        step.status = "failed"
        step.end_time = time.time()
        step.error = error
        return step
    
    def complete(self, answer: str, confidence: float):
        """Complete the session."""
        self.answer = answer
        self.confidence = confidence
        self.end_time = time.time()
    
    @property
    def duration(self) -> float:
        """Get session duration in seconds."""
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time


@dataclass
class ImpactItem:
    """A single code location affected by a library change, CVE, or API migration."""
    file_path: str
    line_number: int
    pattern: str  # the code snippet that matches
    severity: str  # "HIGH", "MEDIUM", "LOW"
    action: str  # suggested migration or fix
    entity: str  # the library entity that's affected (e.g., "Session.execute")


@dataclass
class ImpactAnalysis:
    """Structured impact analysis result for a research query."""
    query: str
    entity_count: int
    affected_files: List[str]
    items: List[ImpactItem] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)