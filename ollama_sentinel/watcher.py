"""
File watcher for Ollama Sentinel.
"""
import asyncio
import logging
import pathlib
import time
from typing import Dict, Optional, Set

import pathspec
from watchfiles import awatch, Change

from .config import load_config
from .extractor import extract_findings_legacy, validate_findings
from .models import SentinelConfig
from .processor import FileChange, FileProcessor
from .violation_db import ViolationDB

log = logging.getLogger("ollama-sentinel")

# Always-on noise filters applied regardless of user config.
# Covers dotdirs, build artifacts, lock files, binaries, and media.
# Set watch.disable_builtin_ignores: true in config to opt out.
_BUILTIN_IGNORE_PATTERNS: list[str] = [
    # VCS / IDE dotdirs
    "**/.git/**",
    "**/.claude/**",
    "**/.planning/**",
    "**/.build/**",
    "**/.idea/**",
    "**/.vscode/**",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "**/.tox/**",
    "**/.venv/**",
    "**/venv/**",
    "**/.next/**",
    "**/.nuxt/**",
    "**/.cache/**",
    # Build output dirs
    "**/node_modules/**",
    "**/__pycache__/**",
    "**/dist/**",
    "**/build/**",
    "**/target/**",
    "**/.gradle/**",
    "**/DerivedData/**",
    "**/.ollama_reviews/**",
    # Lock / DB / binary extensions
    "**/*.mdb",
    "**/*.lock",
    "**/*.lockb",
    "**/*.db",
    "**/*.sqlite",
    "**/*.sqlite3",
    "**/*.so",
    "**/*.dylib",
    "**/*.dll",
    "**/*.exe",
    "**/*.o",
    "**/*.a",
    "**/*.class",
    "**/*.jar",
    "**/*.pyc",
    "**/*.pyo",
    "**/*.pyd",
    "**/*.zip",
    "**/*.tar",
    "**/*.gz",
    "**/*.bz2",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.ico",
    "**/*.pdf",
    "**/*.woff",
    "**/*.woff2",
    "**/*.ttf",
    "**/*.eot",
    "**/*.mp4",
    "**/*.mp3",
    "**/*.mov",
    # Hidden heartbeat / meta files
    "**/.DS_Store",
    "**/.watcher_heartbeat",
]


def _is_likely_binary(path: pathlib.Path, peek_bytes: int = 8192) -> bool:
    """Return True if the file appears to be binary (contains a null byte in the first peek_bytes)."""
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(peek_bytes)
    except OSError:
        return False


def _should_run_legacy_extractor(grounding: bool, review: dict) -> bool:
    """Decide whether the legacy regex finding extractor should run.

    Runs when grounding is off (model emits free-form prose by design) OR
    when a grounded review failed JSON parse (`grounding_parse_failed`),
    so a model that ignored Ollama's `format` schema still yields findings
    instead of silently persisting none.
    """
    if not grounding:
        return True
    return bool(review.get("grounding_parse_failed"))


class FileSentinel:
    """Main sentinel class that watches for file changes and coordinates processing."""
    
    def __init__(self, config_path: pathlib.Path, *, grounding_override: Optional[bool] = None):
        """
        Initialize the file sentinel.

        Args:
            config_path: Path to configuration file
            grounding_override: If not None, overrides the YAML's
                ``processing.grounding`` value. Used by the CLI's
                ``--no-grounding`` flag for ad-hoc debug runs.
        """
        self.config_path = config_path
        loaded = load_config(config_path)

        if not loaded:
            raise ValueError(f"Failed to load configuration from {config_path}")

        self.config: SentinelConfig = loaded
        if grounding_override is not None:
            self.config.processing.grounding = grounding_override

        # Initialize violation memory if enabled
        self.violation_db = None
        if self.config.memory.enabled:
            db_dir = pathlib.Path(self.config.watch.directory).resolve()
            db_path = db_dir / self.config.memory.db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.violation_db = ViolationDB(str(db_path))

        self.processor = FileProcessor(self.config, violation_db=self.violation_db)
        self.pending_changes: Set[FileChange] = set()
        self.processing_lock = asyncio.Lock()

        # Initialize git ignore patterns
        self._initialize_ignore_spec()
    
    def _initialize_ignore_spec(self):
        """Initialize PathSpec for proper gitignore-style pattern matching."""
        patterns: list[str] = []

        # Prepend built-in noise filters unless explicitly disabled
        if not self.config.watch.disable_builtin_ignores:
            patterns.extend(_BUILTIN_IGNORE_PATTERNS)
            log.info(
                "%d built-in ignore patterns active; "
                "set watch.disable_builtin_ignores: true to opt out",
                len(_BUILTIN_IGNORE_PATTERNS),
            )

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

        # Append user-configured patterns last so they can override/extend
        patterns.extend(self.config.watch.ignore_patterns)

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

        # Enforce size limit (protect Ollama context window and skip large binaries).
        # OSError means the file vanished — skip the size check and let the
        # is_file() guard in the caller handle it.
        max_bytes = self.config.watch.max_file_size_kb * 1024
        try:
            if path.stat().st_size > max_bytes:
                return True
        except OSError:
            pass

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

        if _is_likely_binary(path):
            log.debug("Skipping binary file %s", rel_path)
            return

        log.info(f"Processing {rel_path}")

        try:
            # Generate review (returns dict with "summary" and "findings" keys)
            review = await self.processor.generate_review(file_change, model_role=model_role)

            # Persist findings from the structured review output (best-effort)
            if self.violation_db:
                try:
                    findings_list = review.get("findings", [])
                    valid_findings: list = []
                    if findings_list:
                        # Grounded path: verbatim-validate pre-structured findings.
                        file_content = file_change.content
                        if file_content is None:
                            try:
                                file_content = file_change.path.read_text(errors="replace")
                            except OSError:
                                file_content = ""
                        valid_findings = await validate_findings(
                            findings_list, str(rel_path), file_content,
                        )
                    elif _should_run_legacy_extractor(
                        self.config.processing.grounding, review,
                    ):
                        # Ungrounded by config, OR grounded but the model
                        # ignored the schema and returned prose — regex-extract
                        # findings from the free-form text instead of dropping
                        # them.
                        summary_text = review.get("summary", "")
                        valid_findings = extract_findings_legacy(summary_text, str(rel_path))
                    if valid_findings:
                        await asyncio.to_thread(
                            self.violation_db.persist_findings, str(rel_path), valid_findings,
                        )
                        log.info(f"Persisted {len(valid_findings)} findings for {rel_path}")
                except Exception as e:
                    log.warning(f"Finding persistence failed for {rel_path}: {e}")

                # Refresh the SARIF surface so editors/CI see current findings.
                # Best-effort: a SARIF failure must never block review saving.
                try:
                    from . import __version__
                    from .sarif import generate_sarif_file
                    await asyncio.to_thread(
                        generate_sarif_file,
                        self.violation_db,
                        self.processor.watch_dir,
                        self.processor.output_dir,
                        tool_version=__version__,
                    )
                except Exception as e:
                    log.warning(f"SARIF refresh failed for {rel_path}: {e}")

            # Save review (sync I/O, run in thread to avoid blocking event loop)
            output_path = await asyncio.to_thread(self.processor.save_review, file_change, review)
            log.info(f"Saved review to {output_path}")

            # Output to console if enabled
            summary_text = review.get("summary", "")
            if self.config.output.console_output:
                print("\n" + "=" * 80)
                print(f"Review for {path.name}")
                print("=" * 80)
                print(summary_text)
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
        
        async for changes in awatch(
            watch_dir,
            recursive=self.config.watch.recursive,
            watch_filter=lambda _, path_str: not self._should_ignore(pathlib.Path(path_str)),
        ):
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