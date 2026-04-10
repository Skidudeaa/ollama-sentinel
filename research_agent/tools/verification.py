# research_agent/tools/verification.py
from __future__ import annotations
import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

from langchain_openai import ChatOpenAI

from research_agent.core.models import ContentItem
from research_agent.core.logging import get_logger

logger = get_logger(__name__)

@dataclass
class VerificationResult:
    """Result of verification process."""
    verified: bool
    confidence: float
    critique: str
    hallucinations: List[str]
    improvements: List[str]

class VerificationTool:
    """Advanced answer verification with multi-stage fact checking."""
    
    def __init__(
        self, 
        openai_api_key: str,
        model_name: str = "gpt-4o-preview",
        temperature: float = 0.0
    ):
        self.openai_api_key = openai_api_key
        self.model_name = model_name
        self.temperature = temperature
        
        # Initialize LLM
        self.llm = ChatOpenAI(
            model=model_name,
            temperature=temperature,
            api_key=openai_api_key
        )
    
    def _identify_claims(self, answer: str) -> List[str]:
        """Extract factual claims from answer for verification."""
        prompt = f"""
<system>
You are a precise claim extractor. Your task is to identify the specific factual claims in a text that should be verified.

GUIDELINES:
1. Focus on objective, verifiable claims (not opinions or general knowledge)
2. Extract claims as complete sentences or phrases
3. Prioritize claims about:
   - Statistics, numbers, dates, or measurements
   - Specific details about technologies, systems, or processes
   - Quotes or paraphrased content attributed to specific sources
   - Statements about how specific things work or operate
4. Do not include general background information or universally accepted facts
5. Include claims even if they have citations, as those need verification
</system>

TEXT TO ANALYZE:
{answer}

Extract the top 5-10 factual claims that should be verified. Output ONLY the claims, one per line, with no commentary.
"""
        
        response = self.llm.invoke(prompt)
        claims = [claim.strip() for claim in response.content.strip().split("\n") if claim.strip()]
        return claims
    
    def _verify_claims(self, claims: List[str], sources: List[ContentItem]) -> Tuple[List[str], List[str]]:
        """Verify each claim against sources."""
        # Prepare sources text
        sources_text = "\n\n".join([
            f"SOURCE {i+1} ({s.url}):\n{s.content[:3000]}"
            for i, s in enumerate(sources)
        ])
        
        # Verify claims
        verified_claims = []
        unverified_claims = []
        
        for claim in claims:
            prompt = f"""
<system>
You are a meticulous fact-checker. Your task is to verify if a claim is supported by the provided sources.

GUIDELINES:
1. Check if the claim is explicitly supported by the sources
2. Consider partial matches and paraphrases that convey the same information
3. If the claim contains multiple facts, all must be supported
4. Be strict: the burden of proof is on the sources
5. Give benefit of doubt only for general knowledge or widely accepted facts
</system>

CLAIM TO VERIFY:
"{claim}"

SOURCES:
{sources_text}

Is this claim supported by the sources? Respond with VERIFIED or UNVERIFIED followed by a brief explanation.
"""
            
            response = self.llm.invoke(prompt)
            
            # Determine if verified
            result = response.content.strip()
            if result.startswith("VERIFIED"):
                verified_claims.append(claim)
            else:
                unverified_claims.append(claim)
                
        return verified_claims, unverified_claims
    
    def _calculate_confidence(self, verified: List[str], unverified: List[str]) -> float:
        """Calculate confidence score based on verification results."""
        if not verified and not unverified:
            return 0.5  # No claims to verify
            
        return len(verified) / (len(verified) + len(unverified)) if verified or unverified else 0.5
    
    def _generate_critique(
        self, 
        answer: str, 
        sources: List[ContentItem], 
        verified_claims: List[str], 
        unverified_claims: List[str]
    ) -> str:
        """Generate a detailed critique of the answer."""
        # Prepare inputs
        verified_list = "\n".join([f"- {claim}" for claim in verified_claims])
        unverified_list = "\n".join([f"- {claim}" for claim in unverified_claims])
        
        prompt = f"""
<system>
You are an expert research evaluator providing detailed critique of synthesized answers.

GUIDELINES:
1. Evaluate factual accuracy, comprehensiveness, and clarity
2. Identify specific strengths and weaknesses
3. Suggest concrete improvements
4. Be constructive but rigorous in your assessment
</system>

ANSWER:
{answer}

VERIFICATION RESULTS:
Verified Claims:
{verified_list if verified_claims else "None"}

Unverified Claims:
{unverified_list if unverified_claims else "None"}

Generate a detailed critique of this answer. Focus on factual accuracy, comprehensiveness, and clarity.
Identify specific strengths, weaknesses, and suggest concrete improvements.
"""
        
        response = self.llm.invoke(prompt)
        return response.content.strip()
    
    def _suggest_improvements(self, answer: str, critique: str, unverified_claims: List[str]) -> List[str]:
        """Suggest specific improvements based on critique and unverified claims."""
        prompt = f"""
<system>
You are an expert research advisor providing actionable improvement suggestions.

GUIDELINES:
1. Suggest specific, concrete improvements to address identified issues
2. Focus on factual accuracy, completeness, and clarity
3. Provide suggestions that can be directly implemented
4. Prioritize the most important improvements first
</system>

ANSWER:
{answer}

CRITIQUE:
{critique}

UNVERIFIED CLAIMS:
{unverified_claims}

List 3-5 specific, actionable improvements that would address the most important issues in this answer.
Each suggestion should be clear, specific, and directly implementable. Output as a bulleted list.
"""
        
        response = self.llm.invoke(prompt)
        improvements = [
            line.strip().lstrip("- ")
            for line in response.content.strip().split("\n")
            if line.strip() and line.strip().startswith("-")
        ]
        return improvements
    
    def verify(self, answer: str, sources: List[ContentItem]) -> VerificationResult:
        """Perform multi-stage verification of an answer against sources."""
        logger.info("Starting verification process")
        
        try:
            # Stage 1: Extract claims
            claims = self._identify_claims(answer)
            logger.info(f"Identified {len(claims)} factual claims")
            
            # Stage 2: Verify claims
            verified_claims, unverified_claims = self._verify_claims(claims, sources)
            logger.info(f"Verification results: {len(verified_claims)} verified, {len(unverified_claims)} unverified")
            
            # Stage 3: Calculate confidence
            confidence = self._calculate_confidence(verified_claims, unverified_claims)
            
            # Stage 4: Generate critique
            critique = self._generate_critique(answer, sources, verified_claims, unverified_claims)
            
            # Stage 5: Suggest improvements
            improvements = self._suggest_improvements(answer, critique, unverified_claims)
            
            # Create result
            result = VerificationResult(
                verified=confidence >= 0.7,
                confidence=confidence,
                critique=critique,
                hallucinations=unverified_claims,
                improvements=improvements
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Error during verification: {e}")
            return VerificationResult(
                verified=False,
                confidence=0.0,
                critique=f"Verification error: {str(e)}",
                hallucinations=[],
                improvements=[]
            )