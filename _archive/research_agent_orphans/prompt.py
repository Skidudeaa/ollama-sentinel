# research_agent/utils/prompt.py
from __future__ import annotations
import logging
from typing import Dict, Any, List, Optional
from pybars import Compiler

from research_agent.core.logging import get_logger

logger = get_logger(__name__)

# Initialize Handlebars compiler
compiler = Compiler()

class PromptManager:
    """Manages prompt templates using Handlebars."""
    
    def __init__(self):
        """Initialize prompt manager."""
        self.templates: Dict[str, Any] = {}
        self._load_templates()
    
    def _load_templates(self):
        """Load built-in templates."""
        self.templates = {
            "analyze": compiler.compile("""
Analyze this research query and create a plan:

QUERY: {{query}}

{{#if similar_queries}}
Similar past queries:
{{#each similar_queries}}
- {{this.text}}
{{/each}}
{{/if}}

{{#if code_context}}
CODE CONTEXT:
{{code_context}}
{{/if}}

Determine:
1. The key information needs and concepts to research
2. Best search terms for finding relevant information
3. How the code context relates to the query (if applicable)
4. What specific details will be needed for a complete answer

Output a concise analysis and research plan.
"""),
            
            "search": compiler.compile("""
Based on this analysis, generate 1-3 specific search queries:

ANALYSIS:
{{analysis}}

QUERY: {{query}}

Generate search queries that will find the most relevant information.
Each query should be specific and focused on a different aspect of the information need.
Output just the search queries, one per line, nothing else.
"""),
            
            "code_search": compiler.compile("""
Create a specific code search query based on:

ORIGINAL QUERY: {{query}}

{{#if code_context}}
CODE CONTEXT:
{{code_context}}
{{/if}}

What specific code patterns, implementations, or context should we look for?
Focus on technical terms, function names, or implementation details.
Output just the search query, nothing else.
"""),
            
            "synthesize": compiler.compile("""
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

{{#if code_context}}
CODE CONTEXT:
{{code_context}}
{{/if}}

WEB SOURCES:
{{#each web_sources}}
SOURCE {{@index}}: {{url}}
{{title}}
---
{{content}}

{{/each}}

TASK: Synthesize a comprehensive, accurate answer that integrates web information with code context.
Include inline citations [1], [2], etc. and a REFERENCES section at the end.
Assess your confidence in the final answer on a scale of 0-1.
"""),
            
            "verify": compiler.compile("""
<system>
You are a meticulous fact-checker examining an answer for accuracy against the provided sources.

GUIDELINES:
1. Check if each claim is explicitly supported by the sources
2. Consider partial matches and paraphrases that convey the same information
3. If a claim contains multiple facts, all must be supported
4. Be strict: the burden of proof is on the sources
5. Give benefit of doubt only for general knowledge or widely accepted facts
</system>

ANSWER TO VERIFY:
{{answer}}

SOURCES:
{{#each sources}}
SOURCE {{@index}}: {{url}}
{{content}}

{{/each}}

Check every factual claim in the answer against the sources.
Identify any claims that are:
1. CONTRADICTED by the sources
2. NOT SUPPORTED by the sources
3. VERIFIED by the sources

Then provide a CONFIDENCE score from 0-1 on the overall accuracy.
""")
        }
    
    def render(self, template_name: str, context: Dict[str, Any]) -> str:
        """Render a template with context.
        
        Args:
            template_name: Template name
            context: Context variables
            
        Returns:
            Rendered template
        """
        if template_name not in self.templates:
            logger.warning(f"Template not found: {template_name}")
            return ""
            
        try:
            return self.templates[template_name](context)
        except Exception as e:
            logger.error(f"Error rendering template: {e}")
            return ""
    
    def add_template(self, name: str, template: str):
        """Add a new template.
        
        Args:
            name: Template name
            template: Template string
        """
        try:
            self.templates[name] = compiler.compile(template)
        except Exception as e:
            logger.error(f"Error compiling template: {e}")