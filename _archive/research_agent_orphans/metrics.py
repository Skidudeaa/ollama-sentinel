# research_agent/utils/metrics.py
from __future__ import annotations
import time
import logging
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field

from research_agent.core.logging import get_logger

logger = get_logger(__name__)

@dataclass
class Timer:
    """Simple timer for measuring execution time."""
    
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    
    def stop(self):
        """Stop the timer."""
        self.end_time = time.time()
    
    @property
    def elapsed(self) -> float:
        """Get elapsed time in seconds."""
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time

class PerformanceTracker:
    """Tracks performance metrics for research operations."""
    
    def __init__(self):
        """Initialize performance tracker."""
        self.timers: Dict[str, List[Timer]] = {}
        self.counts: Dict[str, int] = {}
    
    def start_timer(self, operation: str) -> Timer:
        """Start a timer for an operation.
        
        Args:
            operation: Operation name
            
        Returns:
            Timer instance
        """
        timer = Timer()
        
        if operation not in self.timers:
            self.timers[operation] = []
        
        self.timers[operation].append(timer)
        
        return timer
    
    def stop_timer(self, operation: str):
        """Stop the most recent timer for an operation.
        
        Args:
            operation: Operation name
        """
        if operation in self.timers and self.timers[operation]:
            self.timers[operation][-1].stop()
    
    def increment_count(self, counter: str, value: int = 1):
        """Increment a counter.
        
        Args:
            counter: Counter name
            value: Increment value
        """
        self.counts[counter] = self.counts.get(counter, 0) + value
    
    def get_average_time(self, operation: str) -> float:
        """Get average time for an operation.
        
        Args:
            operation: Operation name
            
        Returns:
            Average time in seconds
        """
        if operation not in self.timers or not self.timers[operation]:
            return 0.0
            
        times = [t.elapsed for t in self.timers[operation] if t.end_time]
        
        if not times:
            return 0.0
            
        return sum(times) / len(times)
    
    def get_total_time(self, operation: str) -> float:
        """Get total time for an operation.
        
        Args:
            operation: Operation name
            
        Returns:
            Total time in seconds
        """
        if operation not in self.timers:
            return 0.0
            
        return sum(t.elapsed for t in self.timers[operation] if t.end_time)
    
    def get_count(self, counter: str) -> int:
        """Get counter value.
        
        Args:
            counter: Counter name
            
        Returns:
            Counter value
        """
        return self.counts.get(counter, 0)
    
    def get_report(self) -> Dict[str, Any]:
        """Get performance report.
        
        Returns:
            Performance report dictionary
        """
        report = {
            "timers": {},
            "counts": self.counts.copy()
        }
        
        for operation, timers in self.timers.items():
            report["timers"][operation] = {
                "average": self.get_average_time(operation),
                "total": self.get_total_time(operation),
                "count": len(timers)
            }
            
        return report

# Global performance tracker
PERFORMANCE_TRACKER = PerformanceTracker()

def track_time(operation: str):
    """Decorator to track execution time of a function.
    
    Args:
        operation: Operation name
        
    Returns:
        Decorated function
    """
    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            timer = PERFORMANCE_TRACKER.start_timer(operation)
            try:
                return func(*args, **kwargs)
            finally:
                timer.stop()
        return wrapper
    return decorator

def get_performance_tracker() -> PerformanceTracker:
    """Get global performance tracker.
    
    Returns:
        Global performance tracker
    """
    return PERFORMANCE_TRACKER