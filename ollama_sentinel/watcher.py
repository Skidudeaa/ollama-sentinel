"""
File watcher for Ollama Sentinel.
"""
import asyncio
import logging
import pathlib
import time
from typing import Dict, Set

import pathspec
from watchfiles import awatch, Change

from .config import load_config
from .processor import FileChange, FileProcessor

log = logging.getLogger("ollama-sentinel")


class FileSentinel:
    """Main sentinel class that watches for file changes and coordinates processing."""
    
    def __init__(self, config_path: pathlib.Path):
        """
        Initialize the file sentinel.
        
        Args:
            config_path: Path to configuration file
        """
        self.config_path = config_path
        self.config = load_config(config_path)
        
        if not self.config:
            raise ValueError(f"Failed to load configuration from {config_path}")
        
        self.processor = FileProcessor(self.config)
        self.pending_changes: Set[FileChange] = set()
        self.processing_lock = asyncio.Lock()
        
        # Initialize git ignore patterns
        self._initialize_ignore_spec()
    
    def _initialize_ignore_spec(self):
        """Initialize PathSpec for proper gitignore-style pattern matching."""
        patterns = self.config.watch.ignore_patterns.copy()
        
        # Add patterns from .gitignore if git repository is available
        if hasattr(self.processor, 'repo') and self.processor.repo:
            try:
                gitignore_path = pathlib.Path(self.processor.repo.working_dir) / '.gitignore'
                if gitignore_path.exists():
                    with open(gitignore_path, 'r') as f:
                        gitignore_patterns = [line.strip() for line in f 
                                             if line.strip() and not line.startswith('#')]
                    patterns.extend(gitignore_patterns)
            except Exception as e:
                log.warning(f"Failed to load .gitignore: {e}")
        
        # Create PathSpec object
        self._ignore_spec = pathspec.PathSpec.from_lines('gitwildmatch', patterns)
    
    def _should_ignore(self, path: pathlib.Path) -> bool:
        """
        Check if a file should be ignored.
        
        Args:
            path: Path to check
            
        Returns:
            True if the file should be ignored, False otherwise
        """
        # Always ignore the output directory
        if self.config.output.directory in path.parts:
            return True
        
        # Use PathSpec for matching
        try:
            rel_path = str(path.relative_to(self.processor.watch_dir))
            return self._ignore_spec.match_file(rel_path)
        except ValueError:
            # Path is not relative to watch_dir
            return True
    
    async def process_change(self, file_change: FileChange, model_role: str = "default") -> None:
        """
        Process a single file change.

        Args:
            file_change: File change to process
            model_role: Model role to use for review
        """
        path = file_change.path
        rel_path = path.relative_to(self.processor.watch_dir)

        if not path.is_file() or self._should_ignore(path):
            return

        log.info(f"Processing {rel_path}")

        try:
            # Generate review
            review = await self.processor.generate_review(file_change, model_role=model_role)
            
            # Save review (sync I/O, run in thread to avoid blocking event loop)
            output_path = await asyncio.to_thread(self.processor.save_review, file_change, review)
            log.info(f"Saved review to {output_path}")
            
            # Output to console if enabled
            if self.config.output.console_output:
                # A basic way to display the result in terminal
                print("\n" + "=" * 80)
                print(f"Review for {path.name}")
                print("=" * 80)
                print(review)
                print("=" * 80 + "\n")
        
        except Exception as e:
            log.error(f"Failed to process {rel_path}: {e}")
    
    async def process_pending_changes(self) -> None:
        """Process all pending file changes."""
        async with self.processing_lock:
            if not self.pending_changes:
                return
            
            # Take snapshot of current pending changes
            changes = list(self.pending_changes)
            self.pending_changes.clear()
            
            # Process files concurrently with a limit
            semaphore = asyncio.Semaphore(self.config.processing.max_concurrent_reviews)
            
            async def process_with_semaphore(file_change):
                async with semaphore:
                    await self.process_change(file_change)
            
            await asyncio.gather(*[process_with_semaphore(change) for change in changes])
    
    async def watch_directory(self) -> None:
        """Watch directory for file changes with adaptive debouncing."""
        watch_dir = pathlib.Path(self.config.watch.directory).resolve()
        log.info(f"Watching {watch_dir} for changes")
        
        # Track pending events with timestamps
        pending_events: Dict[pathlib.Path, float] = {}
        
        # Debounce parameters
        debounce_base = self.config.watch.debounce_ms / 1000
        
        async for changes in awatch(watch_dir, recursive=self.config.watch.recursive):
            now = time.monotonic()
            
            # Process new events
            for change_type, path_str in changes:
                path = pathlib.Path(path_str)
                
                if not path.is_file() or self._should_ignore(path):
                    continue
                
                # Update or add timestamp
                pending_events[path] = now
            
            # Wait for minimum debounce period
            await asyncio.sleep(debounce_base)
            
            # Process only files that have been idle for longer than debounce_base
            current_time = time.monotonic()
            stable_files = []
            
            for path, timestamp in list(pending_events.items()):
                time_since_change = current_time - timestamp
                
                # File is stable if idle for debounce_base, or force-flush at debounce_max
                if time_since_change >= debounce_base:
                    stable_files.append(FileChange(path=path, change_type=Change.modified))
                    pending_events.pop(path, None)
            
            # Update pending changes and process them
            if stable_files:
                self.pending_changes.update(stable_files)
                await self.process_pending_changes()
    
    async def run(self) -> None:
        """Run the sentinel."""
        try:
            await self.watch_directory()
        finally:
            await self.processor.close()