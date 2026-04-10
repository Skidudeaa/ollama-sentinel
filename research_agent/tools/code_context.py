# research_agent/tools/code_context.py
from __future__ import annotations
import os
import sys # Import sys
import logging
import re
from typing import List, Dict, Any, Optional, Union, Tuple

from langchain_core.tools import BaseTool
from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.node_parser import SimpleNodeParser
from llama_index.core.schema import Node
from llama_index.core.readers.file.base import SimpleDirectoryReader
from pydantic import BaseModel, Field

from research_agent.utils.embedding import get_embedding_model
from research_agent.utils.cache import Cache
from research_agent.core.logging import get_logger

logger = get_logger(__name__)

class CodeSearchInput(BaseModel):
    query: str = Field(..., description="Query to search for in the codebase")
    file_types: Optional[List[str]] = Field(None, description="File extensions to include (e.g. ['.py', '.js'])")
    exclude_dirs: Optional[List[str]] = Field(None, description="Directories to exclude")

class CodeSearchTool(BaseTool):
    """Enhanced code search with improved parsing and relevance."""
    name: str = "code_search"
    description: str = "Search the codebase for relevant code"
    args_schema: type = CodeSearchInput
    
    repo_path: str = Field("", description="Path to the code repository")
    embedding_model_name: Optional[str] = Field(None, description="Name of the embedding model")
    cache: Optional[Cache] = Field(None, description="Cache for search results")
    chunk_size: int = Field(512, description="Chunk size for code splitting")
    chunk_overlap: int = Field(50, description="Chunk overlap for code splitting")

    def __init__(
        self,
        repo_path: str,
        embedding_model_name: Optional[str] = None,
        cache: Optional[Cache] = None,
        chunk_size: int = 512,
        chunk_overlap: int = 50
    ):
        super().__init__()
        self.repo_path = repo_path
        self.embedding_model_name = embedding_model_name
        self.cache = cache or Cache()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._index = None

    def _initialize_index(self):
        """Initialize the vector index with the codebase."""
        logger.info("Attempting to initialize code index...")
        if self._index is not None:
            logger.info("Index already initialized.")
            return

        # Temporarily increase recursion limit
        original_recursion_limit = sys.getrecursionlimit()
        new_limit = 3000 # Or higher if needed, e.g., 5000
        logger.info(f"Original recursion limit: {original_recursion_limit}, setting to: {new_limit}")
        sys.setrecursionlimit(new_limit)

        # Use cache key based on repo path and embedding model
        cache_key = f"code_index_{self.repo_path}_{self.embedding_model_name}"
        cached_index = self.cache.get(cache_key)

        if cached_index:
            logger.info(f"Loading code index from cache for {self.repo_path}")
            self._index = cached_index
            return

        logger.info(f"Building code index for {self.repo_path}")

        # Define common source code extensions and typical directories to exclude
        source_code_extensions = [".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".sh", ".ipynb"]
        excluded_dirs_patterns = ["./.venv", "./.git", "./node_modules", "./__pycache__", "./.cache", "./build", "./dist", "./target", "./out", "./.vscode", "./.idea", "./*.egg-info"] # Added more common ones

        logger.info("Identifying files for SimpleDirectoryReader...")
        try:
            reader_for_logging = SimpleDirectoryReader(
                self.repo_path,
                required_exts=source_code_extensions,
                exclude_hidden=True,
                recursive=True,
                exclude=excluded_dirs_patterns
            )
            # Corrected logging for input_files
            input_files_list = reader_for_logging.input_files()
            logger.debug(f"Files to be processed by SimpleDirectoryReader ({len(input_files_list)} files): {input_files_list}")
        except Exception as log_e:
            logger.warning(f"Could not list files for logging: {log_e}")

        # Load the documents from the repository
        try:
            logger.info("Loading documents with SimpleDirectoryReader (with filters)...")
            docs = SimpleDirectoryReader(
                self.repo_path,
                required_exts=source_code_extensions,
                exclude_hidden=True,
                recursive=True,
                exclude=excluded_dirs_patterns,
                file_extractor={}  # Use default extractors
            ).load_data()
            logger.info(f"Documents loaded for indexing. Count: {len(docs)}")

            # Get embedding model - local or OpenAI
            logger.info(f"Attempting to get embedding model: {self.embedding_model_name}")
            embed_model = get_embedding_model(self.embedding_model_name)
            logger.info(f"Embedding model obtained: {type(embed_model)}")

            # Configure settings with embedding model and node parser
            logger.info("Configuring SimpleNodeParser...")
            node_parser = SimpleNodeParser.from_defaults(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap
            )
            
            logger.info("SimpleNodeParser configured.")
            
            # Configure global LlamaIndex settings
            logger.info("Setting global LlamaIndex Settings.embed_model...")
            Settings.embed_model = embed_model
            logger.info("Setting global LlamaIndex Settings.node_parser...")
            Settings.node_parser = node_parser
            logger.info("Global LlamaIndex Settings configured.")

            # Build the index using global Settings
            logger.info("Building VectorStoreIndex from documents...")
            self._index = VectorStoreIndex.from_documents(
                docs
            )
            logger.info("VectorStoreIndex built successfully.")

            # Cache the index
            logger.info(f"Caching index with key: {cache_key}")
            self.cache.set(cache_key, self._index)
            logger.info("Index cached successfully.")

        except Exception as e:
            logger.error(f"Error building code index: {e}")
            # Reset recursion limit in case of error too
            sys.setrecursionlimit(original_recursion_limit)
            logger.info(f"Recursion limit reset to {original_recursion_limit} after error.")
            raise
        finally:
            # Always reset recursion limit
            sys.setrecursionlimit(original_recursion_limit)
            logger.info(f"Recursion limit reset to {original_recursion_limit} in finally block.")

    def _preprocess_query(self, query: str) -> str:
        """Enhance the query for better code search results."""
        # Convert natural language queries to more code-specific terms
        code_terms = {
            "how to": "function OR class OR implementation",
            "example of": "example OR implementation OR function",
            "implementation of": "class OR function OR def OR implementation",
            "usage of": "import OR require OR using OR example"
        }

        preprocessed = query
        for term, replacement in code_terms.items():
            if term in query.lower():
                preprocessed = f"{query} {replacement}"
                break

        return preprocessed

    def _format_results(self, nodes: List[Node]) -> str:
        """Format search results for better readability."""
        if not nodes:
            return "No relevant code found."

        formatted_results = []
        for i, node in enumerate(nodes):
            # Extract file path and line numbers
            metadata = node.metadata
            file_path = metadata.get("file_path", "Unknown file")

            # Extract line start and end if available
            line_start = metadata.get("line_start", "?")
            line_end = metadata.get("line_end", "?")
            line_info = f":{line_start}-{line_end}" if line_start != "?" else ""

            # Format the code snippet with syntax highlighting markers
            snippet = node.text.strip()

            # Add to results
            formatted_results.append(
                f"### Source {i+1}: {file_path}{line_info}\n"
                f"```\n{snippet}\n```\n"
            )

        return "\n".join(formatted_results)

    def _run(
        self,
        query: str,
        file_types: Optional[List[str]] = None,
        exclude_dirs: Optional[List[str]] = None
    ) -> str:
        """Run code search query."""
        # Initialize index if needed
        self._initialize_index()

        # Preprocess the query
        processed_query = self._preprocess_query(query)

        # Create query engine with metadata filters if needed
        metadata_filters = {}
        file_type_filter = None
        exclude_filter = None
        if file_types:
            file_type_filter = lambda path: any(path.endswith(ext) for ext in file_types)
        if exclude_dirs:
            exclude_paths = [os.path.join(self.repo_path, d) for d in exclude_dirs]
            exclude_filter = lambda path: not any(path.startswith(ex) for ex in exclude_paths)
        if file_type_filter or exclude_filter:
            metadata_filters["file_path"] = lambda path: (
                (file_type_filter(path) if file_type_filter else True)
                and (exclude_filter(path) if exclude_filter else True)
            )

        # Query the index with similarity search
        query_engine = self._index.as_query_engine(
            similarity_top_k=7,
            metadata_filters=metadata_filters if metadata_filters else None
        )

        try:
            response = query_engine.query(processed_query)

            # Format the response
            if hasattr(response, 'source_nodes') and response.source_nodes:
                return self._format_results(response.source_nodes)
            return "No relevant code found."
        except Exception as e:
            logger.error(f"Error executing code search: {e}")
            return f"Error searching code: {str(e)}"

    async def _arun(
        self,
        query: str,
        file_types: Optional[List[str]] = None,
        exclude_dirs: Optional[List[str]] = None
    ) -> str:
        """Async version of _run."""
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._run, query, file_types, exclude_dirs)