import json
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from providers.factory import get_llm_provider
from providers.base import LLMProvider
from config.settings import settings
from utils.logging import get_logger


logger = get_logger("quality_checker")


@dataclass
class QualityResult:
    score: int
    issues: List[str]
    summary: str
    passed: bool


class QualityChecker:
    def __init__(self, session_id: str, quality_level: str, length_level: str):
        self.session_id = session_id
        self.quality_level = quality_level
        self.length_level = length_level
        self.llm_provider = get_llm_provider()
        self.threshold = settings.QUALITY_THRESHOLD
        self.max_retries = settings.MAX_CONTENT_RETRIES

    def evaluate_slide(
        self,
        slide_number: int,
        title: str,
        narration: str,
        slide_content: Dict[str, Any],
        generation_attempt: int = 1
    ) -> QualityResult:
        prompt = self._build_quality_prompt(
            slide_number, title, narration, slide_content
        )

        try:
            response = self.llm_provider.generate(prompt, temperature=0.3)
            result = self._parse_quality_response(response, slide_number)
            return result
        except Exception as e:
            logger.error(f"[{self.session_id}] Quality check failed for slide {slide_number}: {e}")
            return QualityResult(
                score=0,
                issues=[f"Quality check error: {str(e)}"],
                summary="Quality check failed due to error",
                passed=False
            )

    def _build_quality_prompt(
        self,
        slide_number: int,
        title: str,
        narration: str,
        slide_content: Dict[str, Any]
    ) -> str:
        slide_text = self._format_slide_content(slide_content)

        prompt = f"""You are a quality evaluator for educational video narration. Evaluate the narration for a slide based on the original slide content.

Original Slide Content:
- Slide Number: {slide_number}
- Title: {title}
- Content:
{slide_text}

Generated Narration:
{narration}

EVALUATION CRITERIA (score 0-10):
1. FACTUAL ACCURACY: Does the narration contain factual errors or contradict the slide?
2. HALLUCINATIONS: Does it invent facts, claims, or information not in the slide?
3. UNSUPPORTED CLAIMS: Are there claims that cannot be supported by the slide material?
4. MISSING CONCEPTS: Are important concepts from the slide missing in the narration?
5. RELEVANCE: Is the narration relevant to the slide topic?
6. REPETITION: Is there unnecessary repetition of phrases or ideas?
7. GRAMMAR & CLARITY: Is the narration well-formed, clear, and grammatically correct?
8. QUALITY LEVEL SUITABILITY: Does the language match the target quality level ({self.quality_level})?
9. EDUCATIONAL VALUE: Does it explain concepts rather than just reading the slide?

QUALITY LEVEL CONTEXT:
- beginner: Simple language, explain all terms, use analogies
- intermediate: Standard technical terms, clear but concise
- technical: Precise technical language, implementation details

IMPORTANT: Distinguish between:
(a) Information explicitly present in the slide
(b) Reasonable explanatory context (acceptable)
(c) Claims that cannot be supported by the provided material (hallucination)

Return ONLY valid JSON with this exact structure:
{{
  "score": 8,
  "issues": ["issue1", "issue2"],
  "summary": "Content is technically consistent with the slide."
}}"""

        return prompt

    def _format_slide_content(self, slide_content: Dict[str, Any]) -> str:
        parts = []
        if slide_content.get("title"):
            parts.append(f"Title: {slide_content['title']}")
        if slide_content.get("bullet_points"):
            parts.append("Bullet points: " + "; ".join(slide_content["bullet_points"]))
        if slide_content.get("text_boxes"):
            for tb in slide_content["text_boxes"]:
                parts.append(f"Text: {tb.get('text', '')}")
        if slide_content.get("tables"):
            for table in slide_content["tables"]:
                parts.append(f"Table ({table['rows']}x{table['cols']}): {table['data']}")
        if slide_content.get("notes"):
            parts.append(f"Speaker notes: {slide_content['notes']}")
        return "\n".join(parts) if parts else "(No content)"

    def _parse_quality_response(self, response: str, slide_number: int) -> QualityResult:
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start == -1 or end == 0:
                logger.warning(f"[{self.session_id}] No JSON found in quality response for slide {slide_number}")
                return QualityResult(
                    score=0,
                    issues=["Failed to parse quality response"],
                    summary="Quality check failed - no valid JSON",
                    passed=False
                )

            json_str = response[start:end]
            data = json.loads(json_str)

            score = data.get("score", 0)
            issues = data.get("issues", [])
            summary = data.get("summary", "")

            if not isinstance(score, int) or score < 0 or score > 10:
                logger.warning(f"[{self.session_id}] Invalid score in quality response for slide {slide_number}")
                return QualityResult(
                    score=0,
                    issues=["Invalid score in quality response"],
                    summary="Quality check failed - invalid score",
                    passed=False
                )

            passed = score >= self.threshold

            return QualityResult(
                score=score,
                issues=issues,
                summary=summary,
                passed=passed
            )

        except json.JSONDecodeError as e:
            logger.warning(f"[{self.session_id}] JSON parse error in quality response for slide {slide_number}: {e}")
            return QualityResult(
                score=0,
                issues=[f"JSON parse error: {str(e)}"],
                summary="Quality check failed - JSON parse error",
                passed=False
            )

    def should_retry(self, quality_result: QualityResult, attempt: int) -> bool:
        if quality_result.passed:
            return False
        if attempt >= self.max_retries:
            return False
        return True