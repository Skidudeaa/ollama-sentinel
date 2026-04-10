"""
File processor for Ollama Sentinel.
"""
import asyncio
import datetime
import json
import logging
import os
import pathlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

import git
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from watchfiles import Change

from .models import OutputFormat, SentinelConfig
from .utils import chunk_content_by_lines, generate_diff, safe_read, save_compressed

log = logging.getLogger("ollama-sentinel")


@dataclass
class FileChange:
    """Represents a changed file to be processed."""
    path: pathlib.Path
    change_type: Change
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.now)
    content: Optional[str] = None
    diff: Optional[str] = None
    
    @property
    def file_type(self) -> str:
        """Get the file extension without the dot."""
        return self.path.suffix.lstrip(".").lower() or "txt"
    
    def __hash__(self):
        return hash(str(self.path))


class OllamaClient:
    """Client for interacting with the Ollama API."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=float(config["request_timeout"]), write=5.0, pool=5.0)
        )
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
    
    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True  # Important: reraise the original exception
    )
    async def generate_review(self, model_role: str, prompt: str) -> str:
        """
        Generate a review using the specified Ollama model.
        
        Args:
            model_role: Role of the model to use (e.g., "default", "security")
            prompt: Input prompt for the model
            
        Returns:
            Generated review text
            
        Raises:
            httpx.HTTPError: If the API request fails
        """
        if model_role not in self.config["models"]:
            log.warning(f"Model role '{model_role}' not found, falling back to default")
            model_role = "default"
            
        model_config = self.config["models"][model_role]
        url = f"{self.config['host']}/api/chat"
        
        payload = {
            "model": model_config["name"],
            "messages": [
                {"role": "system", "content": model_config["system_prompt"]},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "temperature": model_config.get("temperature", 0.1),
            "top_p": model_config.get("top_p", 0.9)
        }
        
        if "max_tokens" in model_config:
            payload["max_tokens"] = model_config["max_tokens"]
        
        headers = {"Content-Type": "application/json"}
        
        try:
            response = await self.client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()["message"]["content"]
        except httpx.HTTPError as e:
            log.error(f"Ollama API error: {str(e)}")
            raise  # Re-raise to trigger retry


class FileProcessor:
    """Processes file changes and generates reviews."""
    
    def __init__(self, config: SentinelConfig):
        self.config = config
        self.watch_dir = pathlib.Path(config.watch.directory).resolve()
        self.output_dir = self.watch_dir / config.output.directory
        self.ollama_client = OllamaClient(config.ollama.model_dump())
        self.repo = None
        
        # Try to initialize Git repository if git_diff_mode is enabled
        if config.processing.git_diff_mode:
            try:
                self.repo = git.Repo(self.watch_dir, search_parent_directories=True)
                log.info(f"Git repository found at {self.repo.working_dir}")
            except git.InvalidGitRepositoryError:
                log.warning("Git repository not found, disabling git_diff_mode")
                self.config.processing.git_diff_mode = False
    
    async def close(self):
        """Close the Ollama client."""
        await self.ollama_client.close()
    
    def prepare_file_content(self, file_change: FileChange) -> None:
        """
        Prepare file content or git diff for review.
        
        Args:
            file_change: File change to prepare
        """
        path = file_change.path
        
        if self.config.processing.git_diff_mode and self.repo:
            try:
                # Get the git diff for the file
                rel_path = path.relative_to(pathlib.Path(self.repo.working_dir))
                diff = self.repo.git.diff("HEAD", "--", str(rel_path), 
                                         ignore_blank_lines=True, 
                                         ignore_space_at_eol=True)
                
                if diff.strip():
                    file_change.diff = diff
                else:
                    # If no diff (new file), get the full content
                    file_change.content = safe_read(path, self.watch_dir)
            except (git.GitCommandError, ValueError) as e:
                log.warning(f"Git diff failed for {path}: {e}")
                file_change.content = safe_read(path, self.watch_dir)
        else:
            # Use full file content
            file_change.content = safe_read(path, self.watch_dir)
    
    def chunk_content(self, content: str, file_type: str) -> List[str]:
        """
        Split content into chunks, respecting line boundaries.
        
        Args:
            content: Text content to split
            file_type: File type for language-specific processing
            
        Returns:
            List of content chunks
        """
        max_chars = self.config.processing.max_chars_per_chunk
        overlap = self.config.processing.overlap_chars
        
        if len(content) <= max_chars:
            return [content]
        
        # Use line-based chunking
        return chunk_content_by_lines(content, max_chars, overlap)
    
    def format_prompt(
        self,
        file_change: FileChange,
        chunk_text: Optional[str] = None,
        chunk_index: int = 0,
        total_chunks: int = 1,
    ) -> str:
        """
        Format the prompt for the Ollama model.

        Args:
            file_change: File change to format prompt for
            chunk_text: Pre-computed chunk text (avoids redundant re-chunking)
            chunk_index: Index of the current chunk
            total_chunks: Total number of chunks

        Returns:
            Formatted prompt string
        """
        rel_path = file_change.path.relative_to(self.watch_dir)

        if file_change.diff is not None:
            return f"FILE: {rel_path} (Git Diff)\n```diff\n{file_change.diff}\n```"

        content = chunk_text if chunk_text is not None else file_change.content
        if not content:
            return f"FILE: {rel_path}\n```{file_change.file_type}\n<Empty File>\n```"

        chunk_info = ""
        if total_chunks > 1:
            chunk_info = f" (Part {chunk_index + 1}/{total_chunks})"

        return f"FILE: {rel_path}{chunk_info}\n```{file_change.file_type}\n{content}\n```"
    
    async def generate_review(self, file_change: FileChange, model_role: str = "default") -> str:
        """
        Generate a review for the file change.
        
        Args:
            file_change: File change to review
            model_role: Role of the model to use
            
        Returns:
            Generated review text
        """
        await asyncio.to_thread(self.prepare_file_content, file_change)
        
        if file_change.diff is not None:
            # For git diffs, we don't need chunking
            prompt = self.format_prompt(file_change)
            return await self.ollama_client.generate_review(model_role, prompt)
        
        content = file_change.content
        if not content:
            prompt = self.format_prompt(file_change)
            return await self.ollama_client.generate_review(model_role, prompt)
        
        chunks = self.chunk_content(content, file_change.file_type)

        if len(chunks) == 1:
            prompt = self.format_prompt(file_change, chunk_text=chunks[0])
            return await self.ollama_client.generate_review(model_role, prompt)

        # For multiple chunks, generate review for each concurrently
        async def review_chunk(chunk_idx, total_chunks):
            prompt = self.format_prompt(file_change, chunk_text=chunks[chunk_idx], chunk_index=chunk_idx, total_chunks=total_chunks)
            return await self.ollama_client.generate_review(model_role, prompt)
        
        # Use a semaphore to limit concurrent chunk processing
        max_concurrent_chunks = min(
            len(chunks), 
            self.config.processing.max_concurrent_chunks_per_file
        )
        
        chunk_semaphore = asyncio.Semaphore(max_concurrent_chunks)
        
        async def process_chunk_with_semaphore(chunk_idx, total_chunks):
            async with chunk_semaphore:
                return await review_chunk(chunk_idx, total_chunks)
        
        # Process all chunks concurrently with controlled parallelism
        tasks = [
            process_chunk_with_semaphore(i, len(chunks)) 
            for i in range(len(chunks))
        ]
        
        reviews = await asyncio.gather(*tasks)
        
        # Combine chunk reviews
        combined = "\n\n".join([
            f"## Part {i+1}/{len(chunks)}\n\n{review}" 
            for i, review in enumerate(reviews)
        ])
        
        return f"# Combined Review for {file_change.path.name}\n\n{combined}"
    
    def save_review(self, file_change: FileChange, review: str) -> pathlib.Path:
        """
        Save the review to the output directory.
        
        Args:
            file_change: File change that was reviewed
            review: Generated review text
            
        Returns:
            Path where the review was saved
        """
        rel_path = file_change.path.relative_to(self.watch_dir)
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        
        # Create the output directory structure
        output_path = self.output_dir / rel_path.parent
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Determine file extension based on output format
        extension = ".md"
        if self.config.output.format == OutputFormat.JSON:
            extension = ".json"
        elif self.config.output.format == OutputFormat.HTML:
            extension = ".html"
        
        # Prepare content based on format
        if self.config.output.format == OutputFormat.JSON:
            content = json.dumps({
                "file": str(rel_path),
                "timestamp": timestamp,
                "review": review
            }, indent=2)
        else:
            content = review
        
        # Base filename
        base_filename = f"{rel_path.stem}{extension}"
        
        if self.config.output.history.enabled:
            # Create versioned filename
            compressed_ext = ".gz" if self.config.output.compress else ""
            versioned_filename = f"{rel_path.stem}_{timestamp}{extension}{compressed_ext}"
            versioned_path = output_path / versioned_filename
            
            # Write the review, compressed if configured
            if self.config.output.compress:
                save_compressed(versioned_path, content)
            else:
                versioned_path.write_text(content)
            
            # Create/update the "latest" file
            latest_path = output_path / base_filename
            
            # Create diff if enabled
            if self.config.output.diff_based_history and latest_path.exists():
                try:
                    previous_content = latest_path.read_text()
                    diff_content = generate_diff(previous_content, content, timestamp)
                    diff_path = output_path / f"{rel_path.stem}_latest_diff{extension}"
                    diff_path.write_text(diff_content)
                except Exception as e:
                    log.warning(f"Failed to create diff: {e}")
            
            # Update latest version
            latest_path.write_text(content)
            
            # Cleanup old versions if needed
            if self.config.output.history.max_versions > 0:
                pattern = f"{rel_path.stem}_*{extension}"
                pattern_gz = f"{rel_path.stem}_*{extension}.gz"
                
                versions = sorted([
                    p for p in output_path.glob(pattern) if p.name != base_filename
                ] + [
                    p for p in output_path.glob(pattern_gz)
                ])
                
                while len(versions) > self.config.output.history.max_versions:
                    oldest = versions.pop(0)
                    try:
                        oldest.unlink()
                    except Exception as e:
                        log.error(f"Failed to clean up old version {oldest}: {e}")
            
            return versioned_path
        else:
            # Just save the latest version
            output_file = output_path / base_filename
            output_file.write_text(content)
            return output_file