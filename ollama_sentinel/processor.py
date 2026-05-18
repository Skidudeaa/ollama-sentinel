"""
File processor for Ollama Sentinel.
"""
import asyncio
import datetime
import json
import logging
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union, cast

import git
import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
from watchfiles import Change

from .context.import_resolver import ImportResolver
from .models import OutputFormat, SentinelConfig
from .utils import generate_diff, safe_read, save_compressed

log = logging.getLogger("ollama-sentinel")

_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_start": {"type": "integer"},
                    "line_end": {"type": "integer"},
                    "category": {"type": "string"},
                    "severity": {"type": "string"},
                    "verbatim_excerpt": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["line_start", "line_end", "category", "severity", "verbatim_excerpt", "description"],
            },
        },
    },
    "required": ["summary", "findings"],
}


def _review_format(config) -> Optional[Union[str, Dict[str, Any]]]:
    """Pick the Ollama ``format`` argument based on the grounding flag.

    Returns ``_REVIEW_SCHEMA`` in grounded mode (default) and ``None`` when
    ``--no-grounding`` has flipped ``config.processing.grounding`` off — the
    model then emits free-form prose that the legacy regex extractor handles.
    """
    return _REVIEW_SCHEMA if config.processing.grounding else None


def _is_retryable_ollama_error(exc: BaseException) -> bool:
    """Return True for transient Ollama HTTP errors worth retrying.

    ReadTimeout is intentionally not retried: with stream=False it means the
    model did not finish generation before the configured read timeout, so
    repeating the same prompt usually just creates another long hang.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 408 or status == 429 or status >= 500

    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.NetworkError,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
            httpx.WriteTimeout,
        ),
    )


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
        retry=retry_if_exception(_is_retryable_ollama_error),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def generate_with_model(
        self,
        model_config,
        prompt: str,
        *,
        response_format: Optional[Union[str, Dict[str, Any]]] = None,
    ) -> str:
        """Send `prompt` to Ollama using an explicit model config.

        `model_config` is either an OllamaModelConfig-compatible dict
        (`{"name", "system_prompt", "temperature", "top_p", "max_tokens"?}`)
        or any object exposing those as attributes (e.g., OllamaModelConfig).
        """
        def _get(k, default=None):
            if isinstance(model_config, dict):
                return model_config.get(k, default)
            return getattr(model_config, k, default)

        name = _get("name")
        if not name:
            raise ValueError("model_config missing 'name'")
        system_prompt = _get("system_prompt", "")
        temperature = _get("temperature", 0.1)
        top_p = _get("top_p", 0.9)
        think = _get("think", None)
        max_tokens = _get("max_tokens", None)
        if max_tokens is None:
            max_tokens = _get("output_reserve_tokens", None)

        url = f"{self.config['host']}/api/chat"
        options = {
            "temperature": temperature,
            "top_p": top_p,
        }
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload = {
            "model": name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": options,
        }
        if response_format is not None:
            payload["format"] = response_format
        if think is not None:
            payload["think"] = think

        headers = {"Content-Type": "application/json"}
        try:
            response = await self.client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()["message"]["content"]
        except httpx.ReadTimeout:
            log.error(
                "Ollama API read timeout after %ss for model %s "
                "(num_predict=%s)",
                self.config["request_timeout"],
                name,
                options.get("num_predict", "unset"),
                exc_info=True,
            )
            raise
        except httpx.HTTPError as e:
            log.error(
                "Ollama API error (%s): %s",
                type(e).__name__,
                str(e) or "(no message)",
                exc_info=True,
            )
            raise

    async def generate_review(
        self,
        model_role: str,
        prompt: str,
        *,
        response_format: Optional[Union[str, Dict[str, Any]]] = None,
    ) -> str:
        """Send `prompt` to Ollama using a role name looked up in config.

        ``response_format`` accepts either ``"json"`` (Ollama's loose JSON
        mode) or a JSON Schema dict (Ollama's strict structured-output
        mode, ≥ v0.5.0). Either is forwarded verbatim as the ``format``
        payload key.
        """
        if model_role not in self.config["models"]:
            log.warning(f"Model role '{model_role}' not found, falling back to default")
            model_role = "default"
        return await self.generate_with_model(
            self.config["models"][model_role],
            prompt,
            response_format=response_format,
        )


class _DiskcacheAdapter:
    """Adapts diskcache.Cache to the _CacheLike Protocol used by OllamaEmbedder.

    OllamaEmbedder calls .set(key, value, ttl=...); diskcache calls it .set(key, value, expire=...).

    Note on serialization: diskcache uses pickle by default. This deviates from
    the project's "Cache uses JSON serialization" security guarantee, which
    exists to prevent pickle deserialization attacks on content fetched from
    untrusted web sources. That guarantee is still honored in
    research_agent.utils.cache.Cache. Here it's safe because embedding vectors
    are generated locally from local-filesystem content and never crossed with
    untrusted bytes.
    """

    def __init__(self, path: str):
        import diskcache
        self._cache = diskcache.Cache(path)

    def get(self, key):
        return self._cache.get(key)

    def set(self, key, value, ttl=None):
        self._cache.set(key, value, expire=ttl)
        return True

    def close(self):
        self._cache.close()


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
                self._cache = _DiskcacheAdapter(str(self.output_dir / ".embed_cache"))
            except Exception:
                self._cache = None
            try:
                self.embedder = OllamaEmbedder(
                    host=config.ollama.host,
                    model=cast(str, config.embedding.models["hot"]),
                    cache=self._cache,
                    timeout_seconds=float(config.embedding.timeout_seconds),
                )
                self.retriever = SemanticRetriever(embedder=self.embedder)
            except Exception:
                self.embedder = None
                self.retriever = NullRetriever()

        # Structural recall: AST-based import resolver to surface findings
        # from a file's 1-hop import neighbors. Now lives in
        # ollama_sentinel.context — no [research] extras dependency.
        self._import_resolver: Optional[ImportResolver] = None
        if config.memory.structural_recall:
            try:
                self._import_resolver = ImportResolver(str(self.watch_dir))
            except Exception as e:
                log.warning(
                    "Structural recall unavailable (%s); falling back without it.", e,
                )

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
        if self._cache is not None and hasattr(self._cache, "close"):
            self._cache.close()

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
            grounding=self.config.processing.grounding,
        )

    async def _get_ranked_prior_violations(
        self, file_path: pathlib.Path, *, file_content: Optional[str]
    ) -> Optional[List[dict]]:
        """Fetch prior violations through three layered recall strategies.

        Tries each in order and returns the first non-empty result:
          1. Semantic recall — cosine similarity of file content against all
             unresolved findings, top-k.
          2. Structural recall — findings from this file's 1-hop import
             neighbors (callers and callees), Python-only.
          3. Single-file recall — unresolved findings for this file alone.

        Each layer is independently gated by config and degrades silently on
        failure so review generation is never blocked by a recall error.
        """
        if not self.violation_db:
            return None

        rel = str(file_path.relative_to(self.watch_dir))

        # Layer 1: semantic recall.
        if (self.config.memory.semantic_recall
                and self.embedder is not None
                and file_content):
            try:
                violations = await self.violation_db.get_neighbors_by_similarity(
                    query_text=file_content,
                    embedder=self.embedder,
                    k=self.config.memory.neighbor_k,
                )
                if violations:
                    return violations
            except Exception as e:
                log.warning("Semantic recall failed (%s); falling through.", e)

        # Layer 2: structural recall via 1-hop import graph.
        if self._import_resolver is not None:
            try:
                neighbors = await self._resolve_import_neighbors(file_path)
                if neighbors:
                    violations = await asyncio.to_thread(
                        self.violation_db.get_neighbors_unresolved, neighbors,
                    )
                    if violations:
                        return violations
            except Exception as e:
                log.warning("Structural recall failed (%s); falling through.", e)

        # Layer 3: single-file recall (always tried last).
        try:
            violations = await asyncio.to_thread(
                self.violation_db.get_unresolved, rel,
            )
            return violations if violations else None
        except Exception as e:
            log.warning(
                "Single-file recall failed (%s); continuing without prior violations.", e,
            )
            return None

    async def _resolve_import_neighbors(
        self, file_path: pathlib.Path,
    ) -> List[str]:
        """Return relative paths for *file_path* and its 1-hop import neighbors.

        Resolves both directions:
          - Files imported by *file_path* (callees).
          - Files in the repo that import *file_path* (callers).

        Paths are returned relative to ``self.watch_dir`` to match
        ``ViolationDB``'s storage convention. Files outside ``watch_dir`` are
        silently dropped. Returns ``[]`` if the resolver is unavailable or
        *file_path* is not Python — letting Layer 3 take over for those cases.
        """
        # Resolver is Python-only; non-Python files skip Layer 2 entirely.
        if self._import_resolver is None or file_path.suffix != ".py":
            return []

        rel = str(file_path.relative_to(self.watch_dir))

        def _scan() -> List[str]:
            try:
                imports = self._import_resolver.resolve_imports(str(file_path))
                dependents = self._import_resolver.resolve_dependents(str(file_path))
            except Exception:
                # Any resolver error — return self only so Layer 2 still gives
                # us at least the file's own findings.
                return [rel]

            all_abs = {str(file_path)}
            all_abs.update(imports)
            all_abs.update(dependents)

            result: List[str] = []
            for p in all_abs:
                try:
                    r = str(pathlib.Path(p).relative_to(self.watch_dir))
                    result.append(r)
                except ValueError:
                    # Path lives outside watch_dir; ViolationDB won't have rows for it.
                    pass
            return result

        return await asyncio.to_thread(_scan)

    def _parse_review_response(self, raw: str, *, grounding: bool = True) -> dict[str, Any]:
        """Parse Ollama's response into a structured review dict.

        In grounded mode (``grounding=True``), expects schema-conformant JSON.
        Any non-conformant grounded output (parse failure, valid JSON missing
        the ``findings`` array, or non-dict JSON) returns prose with empty
        findings AND ``grounding_parse_failed=True`` so the watcher degrades
        to the legacy regex extractor. In ungrounded mode, treats ``raw`` as
        free-form prose. Either way, prose findings are extracted downstream
        by the legacy regex path in ``watcher.FileSentinel.process_change``.
        """
        if not grounding:
            return {"summary": raw, "findings": []}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "summary" in parsed and "findings" in parsed:
                return parsed  # type: ignore[return-value]
            if isinstance(parsed, dict) and "summary" in parsed:
                # Valid JSON but schema-ignoring (no `findings` array — a
                # required key). Same signal as a parse failure: degrade so
                # the watcher runs the legacy extractor on the prose.
                return {
                    "summary": parsed["summary"],
                    "findings": [],
                    "grounding_parse_failed": True,
                }
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            # The model ignored Ollama's `format` schema (common for :cloud
            # models and markdown-instructed system prompts) and returned
            # prose. Recoverable: the watcher degrades to the legacy regex
            # extractor on the prose. WARNING, not ERROR — handled path.
            log.warning(
                "Grounded review did not parse as JSON (%s); "
                "degrading to legacy prose extractor", e,
            )
            return {"summary": raw, "findings": [], "grounding_parse_failed": True}
        # Reached only under grounding (ungrounded returned early): JSON
        # parsed but is not a dict / lacks `summary`. Non-conformant —
        # degrade like the other schema-ignoring paths.
        return {"summary": raw, "findings": [], "grounding_parse_failed": True}

    async def generate_review(self, file_change: FileChange, model_role: str = "default") -> dict[str, Any]:
        """
        Generate a review for the file change.

        Args:
            file_change: File change to review
            model_role: Role of the model to use

        Returns:
            A dict with ``summary`` (str) and ``findings`` (list[dict]).
        """
        await asyncio.to_thread(self.prepare_file_content, file_change)
        prior = await self._get_ranked_prior_violations(
            file_change.path, file_content=file_change.content,
        )

        if file_change.diff is not None:
            prompt = await self.format_prompt(file_change, prior_violations=prior)
            raw = await self.ollama_client.generate_review(
                model_role, prompt, response_format=_review_format(self.config),
            )
            return self._parse_review_response(raw, grounding=self.config.processing.grounding)

        content = file_change.content
        if not content:
            prompt = await self.format_prompt(file_change, prior_violations=prior)
            raw = await self.ollama_client.generate_review(
                model_role, prompt, response_format=_review_format(self.config),
            )
            return self._parse_review_response(raw, grounding=self.config.processing.grounding)

        chunks = self.chunk_content(content, file_change.file_type)

        if len(chunks) == 1:
            prompt = await self.format_prompt(
                file_change, chunk_text=chunks[0], prior_violations=prior,
            )
            raw = await self.ollama_client.generate_review(
                model_role, prompt, response_format=_review_format(self.config),
            )
            return self._parse_review_response(raw, grounding=self.config.processing.grounding)

        async def review_chunk(chunk_idx, total_chunks):
            violations = prior if chunk_idx == 0 else None
            prompt = await self.format_prompt(
                file_change,
                chunk_text=chunks[chunk_idx],
                chunk_index=chunk_idx,
                total_chunks=total_chunks,
                prior_violations=violations,
            )
            raw = await self.ollama_client.generate_review(
                model_role, prompt, response_format=_review_format(self.config),
            )
            return self._parse_review_response(raw, grounding=self.config.processing.grounding)

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
        reviews: list[dict[str, Any]] = await asyncio.gather(*tasks)

        summaries = [r.get("summary", "") for r in reviews]
        all_findings: list[dict[str, Any]] = []
        for r in reviews:
            all_findings.extend(r.get("findings", []))

        combined = "\n\n".join([
            f"## Part {i+1}/{len(chunks)}\n\n{s}"
            for i, s in enumerate(summaries)
        ])
        return {
            "summary": f"# Combined Review for {file_change.path.name}\n\n{combined}",
            "findings": all_findings,
        }

    def save_review(self, file_change: FileChange, review: dict[str, Any]) -> pathlib.Path:
        """
        Save the review to the output directory.

        Args:
            file_change: File change that was reviewed
            review: A dict with ``summary`` (str) and ``findings`` (list[dict]).

        Returns:
            Path where the review was saved
        """
        review_text = review.get("summary", "")
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
                "review": review_text,
            }, indent=2)
        else:
            content = review_text

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
