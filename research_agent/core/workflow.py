# research_agent/core/workflow.py
from __future__ import annotations
import ast
import asyncio
import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, cast, TypedDict
from contextlib import nullcontext

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph

from research_agent.tools.search import SearchTool, SearchEngine
from research_agent.tools.browser import BrowserTool
from research_agent.tools.code_context import CodeSearchTool
from research_agent.tools.memory import EnhancedMemoryStore, WebPage, SearchQuery
from research_agent.tools.synthesis import SynthesisTool
from research_agent.tools.verification import VerificationTool
from research_agent.tools.import_resolver import ImportResolver
from research_agent.utils.cache import Cache
from research_agent.core.models import (
    ResearchSession, ContentItem, ImpactAnalysis, ImpactItem,
)
from research_agent.tools.search import SearchResult
from research_agent.core.logging import get_logger

logger = get_logger(__name__)

# Define the state structure for the graph
class AgentState(TypedDict):
    session: ResearchSession
    step: str
    search_results: Optional[List[SearchResult]]
    content_items: Optional[List[ContentItem]]
    code_results: Optional[str]
    answer: Optional[str]
    confidence: Optional[float]
    verification: Optional[Any] # Replace Any with the actual type of verification_result if known (e.g., a Pydantic model)
    refined_queries: Optional[List[str]]
    final_answer: Optional[str]
    impact_analysis: Optional[ImpactAnalysis]

def build_workflow(
    config: Dict[str, Any],
    repo_path: str,
    openai_api_key: str,
    serpapi_api_key: Optional[str] = None,
    telemetry: Optional[Any] = None
) -> Tuple[StateGraph, Dict[str, Any]]:
    """Build the research workflow graph."""
    logger.info("Building research workflow")
    
    # Initialize cache
    cache = Cache(
        cache_dir=config["memory"]["cache_path"],
        ttl_hours=config["memory"]["cache_ttl_hours"]
    )
    
    # Optional: build a shared embedder for semantic ranking (memory + synthesis).
    _embedder = None
    embed_cfg = config.get("embedding", {})
    if embed_cfg.get("enabled", False):
        try:
            from ollama_sentinel.context import OllamaEmbedder
            _embedder = OllamaEmbedder(
                host=embed_cfg.get("host", "http://localhost:11434"),
                model=embed_cfg.get("model", "nomic-embed-text"),
                cache=cache,
            )
        except Exception as e:
            logger.warning("Failed to initialize embedder (%s); falling back to token-overlap.", e)
            _embedder = None

    # Initialize components
    memory = EnhancedMemoryStore(
        openai_api_key=openai_api_key,
        db_path=config["memory"]["db_path"],
        cache=cache,
        embedder=_embedder,
    )

    search_tool = SearchTool(
        serpapi_api_key=serpapi_api_key,
        default_engine=SearchEngine(config["search"]["primary_engine"]),
        cache=cache,
        results_per_query=config["search"]["results_per_query"]
    )

    browser_tool = BrowserTool(
        headless=config["browser"]["headless"],
        user_agent=config["browser"]["user_agent"],
        cache=cache,
        extraction_methods=config["browser"]["extraction_methods"],
        max_content_per_page=config["browser"]["max_content_per_page"],
        enable_javascript=config["browser"]["enable_javascript"],
        page_load_timeout=config["browser"]["page_load_timeout"]
    )

    code_tool = CodeSearchTool(
        repo_path=repo_path,
        embedding_model_name=config["api"]["local_embedding_model"] if config["api"]["use_local_embeddings"] else None,
        cache=cache
    )

    synthesis_tool = SynthesisTool(
        openai_api_key=openai_api_key,
        model_name=config["api"]["openai_model"],
        temperature=config["agent"]["synthesis_temperature"],
        total_budget=config["api"].get("synthesis_context_tokens", 12000),
        embedder=_embedder,
    )
    
    verification_tool = VerificationTool(
        openai_api_key=openai_api_key,
        model_name=config["api"]["openai_model"],
        temperature=config["agent"]["verification_temperature"]
    )
    
    llm = ChatOpenAI(
        model=config["api"]["openai_model"],
        temperature=0,
        api_key=openai_api_key
    )
    
    # Define workflow nodes
    
    def analyze(state: AgentState) -> AgentState:
        """Plan the research approach based on the query."""
        # Get session from state
        session = state["session"]
        step = session.start_step("analyze")
        
        try:
            # Check for similar questions in memory (semantic when embedder available)
            similar_queries = memory.find_similar_queries_sync(session.query)
            
            similar_queries_text = ""
            if similar_queries:
                similar_queries_text = "Similar past queries:\n" + "\n".join([
                    f"- {q.text}" for q in similar_queries
                ])
            
            # Generate analysis
            prompt = f"""
Analyze this research query and create a plan:

QUERY: {session.query}

{similar_queries_text}

{f'CODE CONTEXT: {session.code_context}' if session.code_context else ''}

Determine:
1. The key information needs and concepts to research
2. Best search terms for finding relevant information
3. How the code context relates to the query (if applicable)
4. What specific details will be needed for a complete answer

Output a concise analysis and research plan.
"""
            
            with telemetry.span("generate_analysis") if telemetry else nullcontext():
                analysis = llm.invoke(prompt).content
            
            # Update state
            step.output = analysis
            session.complete_step("analyze", analysis)
            state["step"] = "search"
            return state
            
        except Exception as e:
            logger.error(f"Error in analyze step: {e}")
            session.fail_step("analyze", str(e))
            # Ensure all AgentState keys are present on error
            current_state_snapshot = {k: state.get(k) for k in AgentState.__annotations__}
            current_state_snapshot["session"] = state["session"] # session is always present
            current_state_snapshot["step"] = "failed"
            return cast(AgentState, current_state_snapshot)
    
    def search(state: AgentState) -> AgentState:
        """Execute search and find relevant sources."""
        # Get session from state
        session = state["session"]
        step = session.start_step("search")
        
        try:
            # Generate optimized search queries
            analysis = session.get_step("analyze").output
            
            prompt = f"""
Based on this analysis, generate 1-3 specific search queries:

ANALYSIS:
{analysis}

QUERY: {session.query}

Generate search queries that will find the most relevant information.
Each query should be specific and focused on a different aspect of the information need.
Output just the search queries, one per line, nothing else.
"""
            
            with telemetry.span("generate_search_queries") if telemetry else nullcontext():
                search_queries_text = llm.invoke(prompt).content
            
            # Parse search queries
            search_queries = [q.strip() for q in search_queries_text.strip().split("\n") if q.strip()]
            if not search_queries:
                search_queries = [session.query]  # Fallback to original query
            
            # Execute searches
            all_results = []
            for query in search_queries:
                with telemetry.span("execute_search", {"query": query}) if telemetry else nullcontext():
                    results = search_tool._run(query)
                    all_results.extend(results)
                    
                    # Store query in memory
                    memory.add_search_query(SearchQuery(
                        text=query,
                        results=[r.url for r in results]
                    ))
            
            # Deduplicate results
            seen_urls = set()
            unique_results = []
            
            for result in all_results:
                if result.url not in seen_urls:
                    seen_urls.add(result.url)
                    unique_results.append(result)
            
            # Select top URLs to visit
            top_results = unique_results[:config["search"]["synthesis_sources"]]
            
            # Update state
            step.output = top_results
            session.complete_step("search", top_results)
            state["search_results"] = top_results
            state["step"] = "read"
            return state
            
        except Exception as e:
            logger.error(f"Error in search step: {e}")
            session.fail_step("search", str(e))
            current_state_snapshot = {k: state.get(k) for k in AgentState.__annotations__}
            current_state_snapshot["session"] = state["session"]
            current_state_snapshot["step"] = "failed"
            return cast(AgentState, current_state_snapshot)
    
    # Convert async read function to a synchronous function
    def read(state: AgentState) -> AgentState:
        """Read and extract content from search results."""
        # Get session from state
        session = state["session"]
        step = session.start_step("read")
        
        try:
            # Get top URLs to visit
            search_results = state["search_results"]
            urls = [result.url for result in search_results]
            
            # Use asyncio to run the async function in a synchronous context
            content_items = []
            try:
                # Create a new event loop for this function call
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # Run the fetch_multiple coroutine in the loop
                with telemetry.span("fetch_content", {"url_count": len(urls)}) if telemetry else nullcontext():
                    content_items = loop.run_until_complete(browser_tool.fetch_multiple(urls))

                # Clean up
                loop.run_until_complete(browser_tool._close())
                loop.close()
            except Exception as e:
                logger.error(f"Error running async browser operations: {e}")
                raise
            
            # Store in memory
            for item in content_items:
                if item.content:  # Only store pages with content
                    memory.add_webpage(WebPage(
                        url=item.url,
                        title=item.title,
                        summary=item.content[:200] if item.content else "",
                        content=item.content,
                        archived=item.archived
                    ))
            
            # Remove empty or failed items
            valid_items = [item for item in content_items if item.content]
            
            # Update state
            step.output = valid_items
            session.complete_step("read", valid_items)
            session.sources = valid_items
            state["content_items"] = valid_items
            state["step"] = "code_search"
            return state
            
        except Exception as e:
            logger.error(f"Error in read step: {e}")
            session.fail_step("read", str(e))
            current_state_snapshot = {k: state.get(k) for k in AgentState.__annotations__}
            current_state_snapshot["session"] = state["session"]
            current_state_snapshot["step"] = "failed"
            # content_items might be partially populated or None, state.get handles this
            return cast(AgentState, current_state_snapshot)
    
    def code_search(state: AgentState) -> AgentState:
        """Search code context for relevant information."""
        # Get session from state
        session = state["session"]
        step = session.start_step("code_search")
        
        try:
            # Generate code search query
            prompt = f"""
Create a specific code search query based on:

ORIGINAL QUERY: {session.query}

{f'CODE CONTEXT: {session.code_context}' if session.code_context else ''}

What specific code patterns, implementations, or context should we look for?
Focus on technical terms, function names, or implementation details.
Output just the search query, nothing else.
"""
            
            with telemetry.span("generate_code_query") if telemetry else nullcontext():
                code_query = llm.invoke(prompt).content.strip()
            
            # Execute code search
            with telemetry.span("execute_code_search") if telemetry else nullcontext():
                code_results = code_tool._run(code_query)
            
            # Update state
            step.output = code_results
            session.complete_step("code_search", code_results)
            state["code_results"] = code_results
            state["step"] = "impact_scan"
            return state

        except Exception as e:
            logger.error(f"Error in code search step: {e}")
            session.fail_step("code_search", str(e))
            current_state_snapshot = {k: state.get(k) for k in AgentState.__annotations__}
            current_state_snapshot["session"] = state["session"]
            current_state_snapshot["step"] = "failed"
            return cast(AgentState, current_state_snapshot)

    def impact_scan(state: AgentState) -> AgentState:
        """Scan the repository for code locations affected by the researched topic."""
        session = state["session"]
        step = session.start_step("impact_scan")

        try:
            # ----------------------------------------------------------
            # Unit 10: check cache before running full analysis
            # ----------------------------------------------------------
            query_normalized = re.sub(r"\s+", "_", session.query.strip().lower())
            cache_key = f"impact_{query_normalized}"
            cached = cache.get(cache_key)

            if cached and isinstance(cached, dict) and "analysis" in cached:
                stored_mtimes = cached.get("file_mtimes", {})
                files_unchanged = True
                for fpath, old_mtime in stored_mtimes.items():
                    try:
                        if os.path.getmtime(fpath) != old_mtime:
                            files_unchanged = False
                            break
                    except OSError:
                        files_unchanged = False
                        break

                if files_unchanged:
                    # Reconstruct ImpactAnalysis from cached dict
                    a = cached["analysis"]
                    items = [
                        ImpactItem(**item_dict) for item_dict in a.get("items", [])
                    ]
                    impact = ImpactAnalysis(
                        query=a["query"],
                        entity_count=a["entity_count"],
                        affected_files=a["affected_files"],
                        items=items,
                        timestamp=a.get("timestamp", 0.0),
                    )
                    logger.info("Using cached impact analysis for query: %s", session.query)
                    step.output = impact
                    session.complete_step("impact_scan", impact)
                    state["impact_analysis"] = impact
                    state["step"] = "synthesize"
                    return state

            # ----------------------------------------------------------
            # Step 1: Use LLM to extract concrete entities
            # ----------------------------------------------------------
            content_summaries = ""
            content_items = state.get("content_items") or []
            for ci in content_items[:5]:
                snippet = (ci.content or "")[:800]
                content_summaries += f"SOURCE ({ci.url}):\n{snippet}\n\n"

            code_results_str = state.get("code_results") or ""

            extract_prompt = f"""Extract concrete code entities (library names, function names, \
class names, method names) that are discussed as changing, deprecated, removed, or \
requiring migration in the following research context.

QUERY: {session.query}

WEB SOURCES:
{content_summaries}

CODE CONTEXT:
{code_results_str[:2000]}

Return ONLY a JSON array of strings, e.g. ["requests.get", "Session.execute", "flask.ext"].
If there are no concrete entities, return an empty array: []
"""
            with telemetry.span("extract_entities") if telemetry else nullcontext():
                raw_entities = llm.invoke(extract_prompt).content.strip()

            # Parse entities from LLM response
            entities: List[str] = []
            # Try to find a JSON array in the response
            match = re.search(r"\[.*?\]", raw_entities, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                    if isinstance(parsed, list):
                        entities = [str(e).strip() for e in parsed if str(e).strip()]
                except (json.JSONDecodeError, ValueError):
                    pass

            # ----------------------------------------------------------
            # Step 2: If no entities, pass through unchanged
            # ----------------------------------------------------------
            if not entities:
                logger.info("No entities extracted for impact scan; skipping.")
                step.output = None
                session.complete_step("impact_scan", None)
                state["impact_analysis"] = None
                state["step"] = "synthesize"
                return state

            # ----------------------------------------------------------
            # Step 3: Build import graph and scan for call sites
            # ----------------------------------------------------------
            resolver = ImportResolver(repo_path)
            import_graph = resolver.build_graph()

            impact_items: List[ImpactItem] = []
            affected_files_set: set[str] = set()

            for entity in entities:
                # Split entity into parts for flexible matching
                entity_parts = entity.split(".")
                # The leaf name is the most specific identifier
                leaf = entity_parts[-1] if entity_parts else entity

                for file_path in import_graph:
                    if not os.path.isfile(file_path):
                        continue

                    try:
                        source_text = Path(file_path).read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue

                    # --- AST-based matching ---
                    try:
                        tree = ast.parse(source_text, filename=file_path)
                    except SyntaxError:
                        tree = None

                    found_lines: List[tuple[int, str]] = []

                    if tree is not None:
                        for node in ast.walk(tree):
                            node_match = False
                            snippet = ""

                            if isinstance(node, ast.Attribute) and node.attr == leaf:
                                node_match = True
                                snippet = f".{node.attr}"
                            elif isinstance(node, ast.Name) and node.id == leaf:
                                node_match = True
                                snippet = node.id
                            elif isinstance(node, ast.Call):
                                func = node.func
                                if isinstance(func, ast.Attribute) and func.attr == leaf:
                                    node_match = True
                                    snippet = f".{func.attr}(...)"
                                elif isinstance(func, ast.Name) and func.id == leaf:
                                    node_match = True
                                    snippet = f"{func.id}(...)"

                            if node_match and hasattr(node, "lineno"):
                                found_lines.append((node.lineno, snippet))

                    # --- Fallback: string matching for non-parseable or missed cases ---
                    if not found_lines:
                        for lineno, line in enumerate(source_text.splitlines(), 1):
                            if leaf in line:
                                stripped = line.strip()
                                if stripped:
                                    found_lines.append((lineno, stripped[:120]))

                    for lineno, pattern in found_lines:
                        affected_files_set.add(file_path)
                        impact_items.append(ImpactItem(
                            file_path=file_path,
                            line_number=lineno,
                            pattern=pattern,
                            severity="LOW",  # default; classified below
                            action="",
                            entity=entity,
                        ))

            # ----------------------------------------------------------
            # Step 4: Classify severity
            # ----------------------------------------------------------
            query_lower = session.query.lower()
            for item in impact_items:
                if any(kw in query_lower for kw in ("removed", "breaking", "delete", "remove", "drop")):
                    item.severity = "HIGH"
                elif any(kw in query_lower for kw in ("deprecated", "deprecate", "warning")):
                    item.severity = "MEDIUM"
                else:
                    item.severity = "LOW"

            # ----------------------------------------------------------
            # Step 5: LLM-suggested migration for HIGH severity items
            # ----------------------------------------------------------
            high_items = [it for it in impact_items if it.severity == "HIGH"]
            if high_items:
                high_summary = "\n".join(
                    f"  {it.file_path}:{it.line_number}  {it.pattern}  (entity: {it.entity})"
                    for it in high_items[:20]
                )
                migration_prompt = f"""These HIGH severity code locations use APIs/functions that \
are being removed or have breaking changes.

QUERY: {session.query}

AFFECTED LOCATIONS:
{high_summary}

For each location, suggest a brief migration action (one line each).
Format: file:line -> action
"""
                with telemetry.span("suggest_migrations") if telemetry else nullcontext():
                    migration_text = llm.invoke(migration_prompt).content.strip()

                # Parse migration suggestions back onto items
                for line in migration_text.splitlines():
                    if "->" in line:
                        parts = line.split("->", 1)
                        action_text = parts[1].strip()
                        # Match by file:line prefix
                        loc = parts[0].strip()
                        for it in high_items:
                            loc_key = f"{it.file_path}:{it.line_number}"
                            if loc_key in loc or os.path.basename(it.file_path) in loc:
                                if not it.action:
                                    it.action = action_text

                # Fill default action for any HIGH items still without one
                for it in high_items:
                    if not it.action:
                        it.action = f"Review and migrate usage of {it.entity}"

            # ----------------------------------------------------------
            # Step 6: Build ImpactAnalysis and set on state
            # ----------------------------------------------------------
            impact = ImpactAnalysis(
                query=session.query,
                entity_count=len(entities),
                affected_files=sorted(affected_files_set),
                items=impact_items,
            )

            # ----------------------------------------------------------
            # Unit 10: persist to cache with file mtimes
            # ----------------------------------------------------------
            file_mtimes: Dict[str, float] = {}
            for fpath in affected_files_set:
                try:
                    file_mtimes[fpath] = os.path.getmtime(fpath)
                except OSError:
                    pass
            cache.set(cache_key, {
                "analysis": asdict(impact),
                "file_mtimes": file_mtimes,
            })

            step.output = impact
            session.complete_step("impact_scan", impact)
            state["impact_analysis"] = impact
            state["step"] = "synthesize"
            return state

        except Exception as e:
            logger.error(f"Error in impact scan step: {e}")
            session.fail_step("impact_scan", str(e))
            current_state_snapshot = {k: state.get(k) for k in AgentState.__annotations__}
            current_state_snapshot["session"] = state["session"]
            current_state_snapshot["step"] = "failed"
            return cast(AgentState, current_state_snapshot)

    def synthesize(state: AgentState) -> AgentState:
        """Synthesize an answer from gathered information."""
        # Get session from state
        session = state["session"]
        step = session.start_step("synthesize")
        
        try:
            # Get inputs, with defaults if None
            content_items = state.get("content_items") or []
            code_results_str = state.get("code_results") or ""
            
            # Prepare code context string
            full_code_context = session.code_context or ""
            if code_results_str:
                full_code_context = f"{full_code_context}\n\n{code_results_str}".strip()
            
            # Generate answer
            with telemetry.span("synthesize_answer") if telemetry else nullcontext():
                synthesis_result = synthesis_tool.synthesize(
                    query=session.query,
                    sources=content_items, # Can be empty list
                    code_context=full_code_context if full_code_context else None,
                    impact_analysis=state.get("impact_analysis"),
                )
            
            # Update state
            step.output = synthesis_result
            session.complete_step("synthesize", synthesis_result)
            state["answer"] = synthesis_result["answer"]
            state["confidence"] = synthesis_result["confidence"]
            state["step"] = "verify"
            return state
            
        except Exception as e:
            logger.error(f"Error in synthesize step: {e}")
            session.fail_step("synthesize", str(e))
            current_state_snapshot = {k: state.get(k) for k in AgentState.__annotations__}
            current_state_snapshot["session"] = state["session"]
            current_state_snapshot["step"] = "failed"
            return cast(AgentState, current_state_snapshot)
    
    def verify(state: AgentState) -> AgentState:
        """Verify the answer for accuracy."""
        # Get session from state
        session = state["session"]
        step = session.start_step("verify")
        
        try:
            # Get inputs, with defaults if None
            answer_to_verify = state.get("answer") # Renamed to avoid confusion with state["answer"]
            content_items_for_verify = state.get("content_items") or [] # Renamed
            
            if not answer_to_verify: # Cannot verify if there's no answer
                logger.warning("No answer to verify. Skipping verification.")
                state["verification"] = None
                state["confidence"] = state.get("confidence", 0.0) # Keep existing confidence or default
                return state

            # Verify answer
            with telemetry.span("verify_answer") if telemetry else nullcontext():
                verification_result = verification_tool.verify(answer_to_verify, content_items_for_verify) # content_items can be empty list
            
            # Update state
            step.output = verification_result
            session.complete_step("verify", verification_result)
            state["verification"] = verification_result
            state["confidence"] = verification_result.confidence
            
            # NOTE: The decision for the next step ("finalize" or "refine")
            # will now be handled by the conditional edge logic, not here.
            return state
            
        except Exception as e:
            logger.error(f"Error in verify step: {e}")
            session.fail_step("verify", str(e))
            current_state_snapshot = {k: state.get(k) for k in AgentState.__annotations__}
            current_state_snapshot["session"] = state["session"]
            # current_state_snapshot["step"] = "failed" # Let router decide based on verification being None
            current_state_snapshot["verification"] = None # Crucial: set verification to None on error
            current_state_snapshot["confidence"] = current_state_snapshot.get("confidence", 0.0)
            return cast(AgentState, current_state_snapshot)
    
    def refine(state: AgentState) -> AgentState:
        """Refine the research with additional searches."""
        # Get session from state
        session = state["session"]
        step = session.start_step("refine")
        
        try:
            # Get verification result, ensure it's not None
            verification = state.get("verification")
            
            if not verification:
                logger.warning("No verification details to refine from. Finalizing.")
                # If no verification, cannot refine. The router should handle this by going to finalize
                # if verification is None. This node should ideally not be reached if verification is None.
                # If it is, we log a warning and suggest finalization by setting the step.
                logger.warning("Refine called without verification details. Attempting to finalize.")
                state["step"] = "finalize"
                state["refined_queries"] = state.get("refined_queries") or [] # Ensure key exists
                return state

            # Generate refined search queries
            prompt = f"""
I need to improve my answer based on these verification issues:

CRITIQUE:
{verification.critique}

UNVERIFIED CLAIMS:
{', '.join(verification.hallucinations)}

SUGGESTED IMPROVEMENTS:
{', '.join(verification.improvements)}

Generate 1-3 specific new search queries that will help address these issues.
Focus on finding information to verify the claims or support the improvements.
Output just the search queries, one per line, nothing else.
"""
            
            with telemetry.span("generate_refined_queries") if telemetry else nullcontext():
                refined_queries_text = llm.invoke(prompt).content
            
            # Parse refined queries
            refined_queries = [q.strip() for q in refined_queries_text.strip().split("\n") if q.strip()]
            
            # Update state
            step.output = refined_queries
            session.complete_step("refine", refined_queries)
            
            # Return to search step with refined queries
            state["refined_queries"] = refined_queries
            state["step"] = "search"
            return state
            
        except Exception as e:
            logger.error(f"Error in refine step: {e}")
            session.fail_step("refine", str(e))
            current_state_snapshot = {k: state.get(k) for k in AgentState.__annotations__}
            current_state_snapshot["session"] = state["session"]
            current_state_snapshot["step"] = "finalize" # Force finalize on error
            current_state_snapshot["refined_queries"] = current_state_snapshot.get("refined_queries") or []
            return cast(AgentState, current_state_snapshot)
    
    def finalize(state: AgentState) -> AgentState:
        """Finalize the answer and research session."""
        # Get session from state
        session = state["session"]
        step = session.start_step("finalize")
        
        try:
            # Get inputs
            current_answer = state.get("answer") # Renamed
            current_confidence = state.get("confidence", 0.0) # Default if None
            verification_details = state.get("verification") # Renamed
            
            final_answer_str = current_answer if current_answer is not None else "No answer could be generated."
            
            # Add verification summary if available
            if verification_details and final_answer_str and (not isinstance(verification_details, str) or "VERIFICATION SUMMARY" not in verification_details) and hasattr(verification_details, 'improvements'):
                # Ensure confidence is float for formatting
                confidence_score = float(current_confidence) if current_confidence is not None else 0.0
                verification_summary_parts = [f"\n\n## VERIFICATION SUMMARY\n\nConfidence score: {confidence_score:.2f}\n"]
                
                if verification_details.improvements: # type: ignore
                    verification_summary_parts.append("Areas that could be improved:\n")
                    for improvement in verification_details.improvements: # type: ignore
                        verification_summary_parts.append(f"- {improvement}\n")
                
                verification_text = "".join(verification_summary_parts)

                # Insert before REFERENCES if present
                if "REFERENCES" in final_answer_str:
                    parts = final_answer_str.split("REFERENCES", 1)
                    final_answer_str = parts[0].rstrip() + verification_text + "\nREFERENCES" + parts[1]
                else:
                    final_answer_str += verification_text
            
            # Update state
            step.output = final_answer_str
            session.complete_step("finalize", final_answer_str)
            # Ensure confidence is a float for session.complete
            final_confidence = float(current_confidence) if current_confidence is not None else 0.0
            session.complete(final_answer_str, final_confidence)
            
            state["final_answer"] = final_answer_str
            state["confidence"] = final_confidence # Ensure confidence in state is also updated
            return state
            
        except Exception as e:
            logger.error(f"Error in finalize step: {e}")
            session.fail_step("finalize", str(e))
            current_state_snapshot = {k: state.get(k) for k in AgentState.__annotations__}
            current_state_snapshot["session"] = state["session"]
            current_state_snapshot["final_answer"] = current_state_snapshot.get("answer", "Error during finalization.") # Fallback
            current_state_snapshot["confidence"] = current_state_snapshot.get("confidence", 0.0)
            return cast(AgentState, current_state_snapshot)
    
    # Define the workflow graph with the AgentState TypedDict
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("analyze", analyze)
    workflow.add_node("search", search)
    workflow.add_node("read", read)
    workflow.add_node("code_search", code_search)
    workflow.add_node("impact_scan", impact_scan)
    workflow.add_node("synthesize", synthesize)
    workflow.add_node("verify", verify)
    workflow.add_node("refine", refine)
    workflow.add_node("finalize", finalize)

    # Define edges
    workflow.add_edge("analyze", "search")
    workflow.add_edge("search", "read")
    workflow.add_edge("read", "code_search")
    workflow.add_edge("code_search", "impact_scan")
    workflow.add_edge("impact_scan", "synthesize")
    workflow.add_edge("synthesize", "verify")
    workflow.add_edge("refine", "search")
    
    # Set entry point
    workflow.set_entry_point("analyze")
    
    # Add conditional edges
    # Make the condition robust to state["verification"] being None
    def verify_router(state: AgentState) -> str:
        verification_status = state.get("verification") # verification is Optional[Any]
        session = state["session"] # session is always present

        if verification_status and getattr(verification_status, "verified", False):
            return "finalize"
        else:
            # If not verified, or verification failed (verification_status is None)
            if len(session.steps) <= config["agent"]["max_iterations"] * 5:  # Limit iterations
                return "refine"
            else:
                # Too many iterations, or verification failed and no more retries
                return "finalize"

    workflow.add_conditional_edges(
        "verify",
        verify_router,
        {
            "finalize": "finalize",
            "refine": "refine",
        }
    )
    
    # Compile the graph
    compiled_graph = workflow.compile()
    
    # Return the compiled graph and components
    components = {
        "memory": memory,
        "search_tool": search_tool,
        "browser_tool": browser_tool,
        "code_tool": code_tool,
        "synthesis_tool": synthesis_tool,
        "verification_tool": verification_tool,
        "cache": cache,
        "embedder": _embedder,
    }
    
    return compiled_graph, components