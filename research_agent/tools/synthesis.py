# research_agent/tools/synthesis.py
from __future__ import annotations
import logging
from typing import List, Dict, Any, Optional
from pybars import Compiler

from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder

from research_agent.core.models import ContentItem, ImpactAnalysis
from research_agent.core.logging import get_logger
from ollama_sentinel.context import (
    NullRetriever,
    OllamaEmbedder,
    SemanticRetriever,
    TokenCounter,
    build_research_context,
)

logger = get_logger(__name__)

# Handlebars compiler for template rendering
compiler = Compiler()

class SynthesisTool:
    """Advanced answer synthesis with templating and structured output."""

    def __init__(
        self,
        openai_api_key: str,
        model_name: str = "gpt-4o-preview",
        temperature: float = 0.1,
        *,
        total_budget: int = 12000,
        embedder: Optional["OllamaEmbedder"] = None,
    ):
        self.openai_api_key = openai_api_key
        self.model_name = model_name
        self.temperature = temperature
        self.total_budget = total_budget
        self.counter = TokenCounter()
        self.retriever = SemanticRetriever(embedder) if embedder is not None else NullRetriever()

        self.llm = ChatOpenAI(
            model=model_name,
            temperature=temperature,
            api_key=openai_api_key,
        )

        self.main_template = compiler.compile("""
<system>
You are a top-tier research synthesis system that creates comprehensive, accurate answers by combining information from web sources and code repositories.

GUIDELINES:
1. Analyze and integrate information from multiple sources
2. Maintain critical thinking through fact triangulation
3. Create well-structured answers with appropriate sections and formatting
4. Use clear, precise language with appropriate technical depth
5. Include direct quotes from sources sparingly and with attribution
6. Structure your answer around the key concepts, not source by source
7. Cite all information with inline numbered references [1], [2], etc.
8. Provide a complete REFERENCES section at the end listing all sources
9. Assess confidence in your final answer on a scale of 0-1
</system>

QUERY: {{query}}

{{assembled_context}}

TASK: Synthesize a comprehensive, accurate answer that integrates web information with code context.
Include inline citations [1], [2], etc. and a REFERENCES section at the end.
Assess your confidence in the final answer on a scale of 0-1.
""")
    
    def _extract_references(self, content: str) -> List[Dict[str, str]]:
        """Extract references from synthesized content."""
        references = []
        if "REFERENCES" in content:
            refs_section = content.split("REFERENCES")[1]
            ref_lines = refs_section.strip().split("\n")
            for line in ref_lines:
                if line.strip() and ("[" in line or "http" in line):
                    references.append({"text": line.strip()})
                    
        return references
    
    def _extract_confidence(self, content: str) -> float:
        """Extract confidence score from synthesized content."""
        confidence = 0.7  # Default confidence
        
        # Look for explicit confidence statements
        confidence_patterns = [
            r"CONFIDENCE[:\s]+(\d+\.\d+)",
            r"confidence[:\s]+(\d+\.\d+)",
            r"confidence score[:\s]+(\d+\.\d+)",
            r"confidence rating[:\s]+(\d+\.\d+)"
        ]
        
        for pattern in confidence_patterns:
            import re
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                try:
                    conf = float(match.group(1))
                    if 0 <= conf <= 1:
                        confidence = conf
                        break
                except ValueError:
                    pass
                    
        return confidence
    
    @staticmethod
    def format_impact_report(impact_analysis: ImpactAnalysis) -> str:
        """Build a structured impact report from an ImpactAnalysis object.

        The report is returned as a plain-text string suitable for use as
        the synthesized answer when impact data is available.
        """
        items = impact_analysis.items
        affected_files = impact_analysis.affected_files

        high = [it for it in items if it.severity == "HIGH"]
        medium = [it for it in items if it.severity == "MEDIUM"]
        low = [it for it in items if it.severity == "LOW"]

        lines: List[str] = [
            f"IMPACT ANALYSIS: {len(items)} call sites across {len(affected_files)} files",
            "",
        ]

        if high:
            lines.append("HIGH SEVERITY (breaking):")
            for it in high:
                lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {it.action}")
            lines.append("")

        if medium:
            lines.append("MEDIUM SEVERITY (deprecated):")
            for it in medium:
                action = it.action if it.action else "Review usage"
                lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {action}")
            lines.append("")

        if low:
            lines.append("LOW SEVERITY (changed):")
            for it in low:
                action = it.action if it.action else "Monitor for changes"
                lines.append(f"  {it.file_path}:{it.line_number}  {it.pattern} -> {action}")
            lines.append("")

        if high:
            lines.append("SUGGESTED FIRST COMMIT:")
            for it in high:
                lines.append(f"  [ ] {it.file_path}:{it.line_number} - {it.action}")
            lines.append("")

        return "\n".join(lines)

    def synthesize(
        self,
        query: str,
        sources: List[ContentItem],
        code_context: Optional[str] = None,
        impact_analysis: Optional[ImpactAnalysis] = None,
    ) -> Dict[str, Any]:
        """Synthesize an answer from sources and code context."""
        logger.info(f"Synthesizing answer for query: {query}")

        # Structured impact output short-circuit (unchanged behavior).
        if impact_analysis is not None and impact_analysis.items:
            report = self.format_impact_report(impact_analysis)
            return {"answer": report, "references": [], "confidence": 0.9}

        try:
            # Build the assembled context via the shared recipe.
            # The recipe is async; this method is sync to preserve the existing
            # LangGraph node contract. Run the coroutine on a fresh loop.
            import asyncio
            # synthesize() is intentionally sync: the LangGraph node that calls it is a
            # plain def. Do NOT call this method from within a running event loop — use
            # an async wrapper or asyncio.run_coroutine_threadsafe() instead.
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                assembled = loop.run_until_complete(
                    build_research_context(
                        query=query,
                        web_sources=sources,
                        code_results=code_context,
                        impact=impact_analysis,
                        counter=self.counter,
                        total_budget=self.total_budget,
                        retriever=self.retriever,
                    )
                )
            finally:
                loop.close()

            prompt = self.main_template({"query": query, "assembled_context": assembled})
            response = self.llm.invoke(prompt)
            content = response.content

            references = self._extract_references(content)
            confidence = self._extract_confidence(content)
            return {"answer": content, "references": references, "confidence": confidence}

        except Exception as e:
            logger.error(f"Error synthesizing answer: {e}")
            return {"answer": f"Error synthesizing answer: {str(e)}", "references": [], "confidence": 0.0}