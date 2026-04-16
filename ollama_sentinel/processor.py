"""
File processor for Ollama Sentinel.
"""
import asyncio
import datetime
import json
import logging
import pathlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import git
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from watchfiles import Change

from .models import OutputFormat, SentinelConfig
from .utils import generate_diff, safe_read, save_compressed

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

    def __init__(self, config: SentinelConfig, violation_db=None):
        self.config = config
        self.watch_dir = pathlib.Path(config.watch.directory).resolve()
        self.output_dir = self.watch_dir / config.output.directory
        self.ollama_client = OllamaClient(config.ollama.model_dump())
        self.violation_db = violation_db
        self.repo = None

        # Context-assembly dependencies (Task 13).
        from ollama_sentinel.context import (
            NullRetriever,
            OllamaEmbedder,
            SemanticRetriever,
            TokenCounter,
        )

        self.counter = TokenCounter()
        self._cache = None
        self.embedder = None
        self.retriever = NullRetriever()

        if config.embedding.enabled and config.memory.semantic_recall:
            try:
                from research_agent.utils.cache import Cache
                self._cache = Cache(cache_dir=str(self.output_dir / ".embed_cache"))
            except Exception:
                self._cache = None
            try:
                self.embedder = OllamaEmbedder(
                    host=config.ollama.host,
                    model=config.embedding.model,
                    cache=self._cache,
                )
                self.retriever = SemanticRetriever(embedder=self.embedder)
            except Exception:
                self.embedder = None
                self.retriever = NullRetriever()

        # Token budget for review prompts.
        default_model = config.ollama.models["default"]
        self.total_budget = max(
            1024,
            default_model.context_window - default_model.output_reserve_tokens,
        )

        if config.processing.git_diff_mode:
            try:
                self.repo = git.Repo(self.watch_dir, search_parent_directories=True)
                log.info(f"Git repository found at {self.repo.working_dir}")
            except git.InvalidGitRepositoryError:
                log.warning("Git repository not found, disabling git_diff_mode")
                self.config.processing.git_diff_mode = False

    async def close(self):
        """Close clients."""
        await self.ollama_client.close()
        if self.embedder is not None:
            await self.embedder.close()

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
        """Split content into chunks using a token budget."""
        from ollama_sentinel.context.assembler import chunk_by_lines

        # Reserve 30% of the context window for the review recipe's overhead
        # (header, prior violations block). Chunks target ~70% of the budget.
        chunk_budget = max(256, int(self.total_budget * 0.70))
        overlap = max(32, chunk_budget // 20)
        return chunk_by_lines(
            content,
            counter=self.counter,
            max_tokens=chunk_budget,
            overlap_tokens=overlap,
        )

    async def format_prompt(
        self,
        file_change: FileChange,
        chunk_text: Optional[str] = None,
        chunk_index: int = 0,
        total_chunks: int = 1,
        prior_violations: Optional[List[dict]] = None,
    ) -> str:
        """Format the review prompt via the shared context recipe."""
        from ollama_sentinel.context import build_review_context

        rel_path = str(file_change.path.relative_to(self.watch_dir))
        chunk_info = f" (Part {chunk_index + 1}/{total_chunks})" if total_chunks > 1 else ""
        content = chunk_text if chunk_text is not None else file_change.content
        return await build_review_context(
            file_rel_path=rel_path,
            file_type=file_change.file_type,
            content=content,
            diff=file_change.diff,
            chunk_info=chunk_info,
            prior_violations=prior_violations or [],
            counter=self.counter,
            total_budget=self.total_budget,
            retriever=self.retriever,
        )

    async def _get_ranked_prior_violations(
        self, file_path: pathlib.Path, *, file_content: Optional[str]
    ) -> Optional[List[dict]]:
        """Fetch prior violations, ranked semantically when possible."""
        if not self.violation_db:
            return None
        try:
            if (self.config.memory.semantic_recall
                    and self.embedder is not None
                    and file_content):
                violations = await self.violation_db.get_neighbors_by_similarity(
                    query_text=file_content,
                    embedder=self.embedder,
                    k=self.config.memory.neighbor_k,
                )
            else:
                rel = str(file_path.relative_to(self.watch_dir))
                violations = await asyncio.to_thread(
                    self.violation_db.get_unresolved, rel,
                )
            return violations if violations else None
        except Exception as e:
            log.warning("Failed to query prior violations (%s); continuing without them.", e)
            return None

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
        prior = await self._get_ranked_prior_violations(
            file_change.path, file_content=file_change.content,
        )

        if file_change.diff is not None:
            prompt = await self.format_prompt(file_change, prior_violations=prior)
            return await self.ollama_client.generate_review(model_role, prompt)

        content = file_change.content
        if not content:
            prompt = await self.format_prompt(file_change, prior_violations=prior)
            return await self.ollama_client.generate_review(model_role, prompt)

        chunks = self.chunk_content(content, file_change.file_type)

        if len(chunks) == 1:
            prompt = await self.format_prompt(
                file_change, chunk_text=chunks[0], prior_violations=prior,
            )
            return await self.ollama_client.generate_review(model_role, prompt)

        async def review_chunk(chunk_idx, total_chunks):
            violations = prior if chunk_idx == 0 else None
            prompt = await self.format_prompt(
                file_change,
                chunk_text=chunks[chunk_idx],
                chunk_index=chunk_idx,
                total_chunks=total_chunks,
                prior_violations=violations,
            )
            return await self.ollama_client.generate_review(model_role, prompt)

        max_concurrent_chunks = min(
            len(chunks), self.config.processing.max_concurrent_chunks_per_file,
        )
        chunk_semaphore = asyncio.Semaphore(max_concurrent_chunks)

        async def process_chunk_with_semaphore(chunk_idx, total_chunks):
            async with chunk_semaphore:
                return await review_chunk(chunk_idx, total_chunks)

        tasks = [
            process_chunk_with_semaphore(i, len(chunks)) for i in range(len(chunks))
        ]
        reviews = await asyncio.gather(*tasks)

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
