import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from providers.factory import get_llm_provider
from providers.base import LLMProvider
from models.database import SessionLocal
from models.models import Slide, SlideStatus, GenerationAttempt
from config.settings import settings
from utils.json_utils import save_json, load_json_safe
from utils.filesystem import ensure_dir
from utils.logging import get_logger
from orchestrator.quality_checker import QualityChecker, QualityResult


logger = get_logger("content_generator")


class ContentGenerator:
    def __init__(self, session_id: str, quality: str, length: str):
        self.session_id = session_id
        self.quality = quality
        self.length = length
        self.db = SessionLocal()
        self.llm_provider = get_llm_provider()
        self.quality_checker = QualityChecker(session_id, quality, length)
        self.llmout_dir = settings.PROCESSING_DIR / "llmout" / session_id
        self.llmout_dir.mkdir(parents=True, exist_ok=True)
        self.parse_dir = settings.PROCESSING_DIR / "parseppt" / session_id
        self.target_word_range = settings.get_target_word_range(length)

    def generate_for_job(self, ppt_job_id: str) -> bool:
        job = self.db.query(Slide).filter(Slide.ppt_job_id == ppt_job_id).first()
        if not job:
            logger.error(f"[{self.session_id}] No slides found for job: {ppt_job_id}")
            return False

        parsed_file = self.parse_dir / f"{job.ppt_job.ppt_filename.replace('.pptx', '')}.json"
        if not parsed_file.exists():
            logger.error(f"[{self.session_id}] Parsed PPT file not found: {parsed_file}")
            return False

        parsed_data = load_json_safe(parsed_file)
        if not parsed_data:
            logger.error(f"[{self.session_id}] Failed to load parsed data: {parsed_file}")
            return False

        ppt_context = self._build_ppt_context(parsed_data)
        slides = self.db.query(Slide).filter(Slide.ppt_job_id == ppt_job_id).order_by(Slide.slide_number).all()

        all_slides_output = []
        for slide in slides:
            # Check if already quality checked (resumability)
            if slide.status == SlideStatus.QUALITY_CHECKED:
                logger.info(f"[{self.session_id}] Slide {slide.slide_number} already quality checked, skipping")
                all_slides_output.append(self._load_existing_slide_output(ppt_job_id, slide.slide_number))
                continue

            parsed_response = self._generate_and_validate_slide_content(slide, parsed_data, ppt_context, parsed_data)
            if parsed_response:
                all_slides_output.append(parsed_response)
            else:
                logger.error(f"[{self.session_id}] Failed to generate validated content for slide {slide.slide_number}")

        self._save_llm_output(ppt_job_id, parsed_data, all_slides_output)
        return True

    def _generate_and_validate_slide_content(
        self,
        slide: Slide,
        parsed_data: Dict[str, Any],
        ppt_context: str,
        full_parsed_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        slide_parsed = next((s for s in parsed_data["slides"] if s["slide_number"] == slide.slide_number), None)
        if not slide_parsed:
            logger.error(f"[{self.session_id}] Slide {slide.slide_number} not found in parsed data")
            return None

        max_retries = settings.MAX_CONTENT_RETRIES
        attempt = 1

        while attempt <= max_retries:
            logger.info(f"[{self.session_id}] Generating content for slide {slide.slide_number} (attempt {attempt}/{max_retries})")
            
            prompt = self._build_prompt(slide_parsed, ppt_context, attempt)

            try:
                response = self.llm_provider.generate(prompt, temperature=0.7)
                narration = self._parse_llm_response(response)
                if narration is None:
                    logger.error(f"[{self.session_id}] Failed to parse LLM response for slide {slide.slide_number}")
                    attempt += 1
                    continue

                # Build response dict with metadata computed in Python
                slide_number = slide_parsed["slide_number"]
                title = slide_parsed.get("title", "Untitled")
                estimated_word_count = len(narration.split())
                parsed_response = {
                    "slide_number": slide_number,
                    "title": title,
                    "narration": narration,
                    "estimated_word_count": estimated_word_count,
                    "target_word_range": self.target_word_range,
                    "quality_score": None,
                    "quality_feedback": None,
                    "generation_attempt": attempt,
                }

                # Save generation attempt
                self._save_generation_attempt(slide, prompt, response, parsed_response, attempt)

                # Quality check
                quality_result = self.quality_checker.evaluate_slide(
                    slide.slide_number,
                    slide_parsed.get("title", "Untitled"),
                    narration,
                    slide_parsed,
                    attempt
                )

                logger.info(f"[{self.session_id}] Quality check for slide {slide.slide_number}: score={quality_result.score}/10, passed={quality_result.passed}")

                if quality_result.passed:
                    # Success - update slide and save
                    slide.status = SlideStatus.QUALITY_CHECKED
                    slide.content_generated = 1
                    slide.quality_score = quality_result.score
                    slide.quality_feedback = quality_result.summary
                    self.db.commit()

                    # Update parsed response with quality info
                    parsed_response["quality_score"] = quality_result.score
                    parsed_response["quality_feedback"] = quality_result.summary
                    parsed_response["generation_attempt"] = attempt

                    logger.info(f"[{self.session_id}] Slide {slide.slide_number} passed quality check (score: {quality_result.score})")
                    return parsed_response
                else:
                    logger.warning(f"[{self.session_id}] Slide {slide.slide_number} failed quality check (score: {quality_result.score}). Issues: {quality_result.issues}")
                    
                    if not self.quality_checker.should_retry(quality_result, attempt):
                        logger.warning(f"[{self.session_id}] Max retries reached for slide {slide.slide_number}, marking as failed")
                        slide.status = SlideStatus.FAILED
                        slide.quality_score = quality_result.score
                        slide.quality_feedback = quality_result.summary
                        self.db.commit()
                        return None

                    attempt += 1

            except Exception as e:
                logger.error(f"[{self.session_id}] Generation failed for slide {slide.slide_number} (attempt {attempt}): {e}")
                attempt += 1

        logger.error(f"[{self.session_id}] All retries exhausted for slide {slide.slide_number}")
        return None

    def _build_ppt_context(self, parsed_data: Dict[str, Any]) -> str:
        ppt_info = parsed_data["ppt"]
        slides = parsed_data["slides"]

        context_parts = [
            f"Presentation: {ppt_info['filename']}",
            f"Total slides: {ppt_info['slide_count']}",
            "",
            "Slide overview:",
        ]

        for s in slides:
            title = s.get("title", f"Slide {s['slide_number']}")
            bullets = s.get("bullet_points", [])
            text_boxes = s.get("text_boxes", [])
            all_text = bullets + [t.get("text", "") for t in text_boxes]
            context_parts.append(f"  Slide {s['slide_number']}: {title} - {'; '.join(all_text[:3])}")

        return "\n".join(context_parts)

    def _build_prompt(self, slide_data: Dict[str, Any], ppt_context: str, attempt: int = 1) -> str:
        min_words = self.target_word_range["min"]
        max_words = self.target_word_range["max"]

        quality_instructions = {
            "beginner": "Use simple language, explain all technical terms, provide analogies and examples. Assume no prior knowledge.",
            "intermediate": "Use standard technical terminology, explain concepts clearly but concisely. Assume some programming/technical background.",
            "technical": "Use precise technical language, include implementation details, edge cases, and best practices. Assume strong technical background.",
        }

        quality_instruction = quality_instructions.get(self.quality, quality_instructions["beginner"])

        slide_text = self._format_slide_text(slide_data)

        # Add regeneration guidance if this is a retry
        regeneration_guidance = ""
        if attempt > 1:
            regeneration_guidance = f"""
REGENERATION ATTEMPT {attempt}: The previous narration failed quality check.
Focus on: factual accuracy, avoiding hallucinations, staying faithful to slide content,
and matching the {self.quality} quality level.
"""

        prompt = f"""You are an expert educator creating narration for an educational video from a PowerPoint presentation.

{ppt_context}

Current slide:
- Number: {slide_data['slide_number']}
- Title: {slide_data.get('title', 'Untitled')}
- Content:
{slide_text}

INSTRUCTIONS:
1. Generate educational narration that EXPLAINS the concepts on this slide, NOT just reading the text.
2. Target audience: {self.quality} level - {quality_instruction}
3. Target duration: {min_words}-{max_words} words (approximately {self.length} length: {min_words//130}-{max_words//130} minutes at 130-150 wpm speaking rate)
4. Stay faithful to the slide content. Do NOT invent facts, claims, or information not supported by the slide.
5. Distinguish between: (a) explicit slide content, (b) reasonable explanatory context, (c) unsupported claims. Only use (a) and (b).
6. Ensure technical correctness. Avoid hallucinations, contradictions, and repetition.
7. Make the narration flow naturally as spoken educational content.
{regeneration_guidance}

Return ONLY a JSON object with a single key "narration" containing the narration text.
Example: {{"narration": "Your narration here..."}}
Do not include any other keys or text outside this JSON.
"""

        return prompt

    def _format_slide_text(self, slide_data: Dict[str, Any]) -> str:
        parts = []
        if slide_data.get("title"):
            parts.append(f"Title: {slide_data['title']}")
        if slide_data.get("bullet_points"):
            parts.append("Bullet points: " + "; ".join(slide_data["bullet_points"]))
        if slide_data.get("text_boxes"):
            for tb in slide_data["text_boxes"]:
                parts.append(f"Text: {tb.get('text', '')}")
        if slide_data.get("tables"):
            for table in slide_data["tables"]:
                parts.append(f"Table ({table['rows']}x{table['cols']}): {table['data']}")
        if slide_data.get("notes"):
            parts.append(f"Speaker notes: {slide_data['notes']}")
        return "\n".join(parts) if parts else "(No content)"

    def _parse_llm_response(self, response: str) -> Optional[str]:
        try:
            # Remove markdown code fences if present
            stripped = response.strip()
            # If starts with ``` and ends with ```, extract inner
            if stripped.startswith("```"):
                lines = stripped.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip().endswith("```"):
                    lines = lines[:-1]
                stripped = "\n".join(lines).strip()
            
            # First, try to parse as JSON and extract narration
            narration = None
            start = stripped.find("{")
            end = stripped.rfind("}") + 1
            if start != -1 and end > start:
                json_str = stripped[start:end]
                try:
                    data = json.loads(json_str)
                    if isinstance(data.get("narration"), str):
                        narration = data["narration"].strip()
                except json.JSONDecodeError:
                    pass  # fallback to plain text below
            
            # If JSON parsing didn't yield a narration, use the stripped string itself
            if narration is None:
                narration = stripped
                logger.debug(f"[{self.session_id}] Using plain text response as narration (after fence stripping)")
            
            # Validate narration
            if not isinstance(narration, str) or len(narration.strip()) < 20:
                logger.warning(f"[{self.session_id}] Narration too short or invalid after cleaning: {narration[:100]!r}")
                return None
            
            return narration.strip()
        except Exception as e:
            logger.warning(f"[{self.session_id}] Unexpected error parsing LLM response: {e}")
            return None

    def _save_generation_attempt(
        self,
        slide: Slide,
        prompt: str,
        response: str,
        parsed_response: Dict[str, Any],
        attempt: int
    ):
        attempt_id = f"{slide.id}_attempt_{attempt}"
        # Check if attempt already exists (resumability)
        existing = self.db.query(GenerationAttempt).filter(GenerationAttempt.id == attempt_id).first()
        if existing:
            existing.prompt = prompt
            existing.response = response
            existing.quality_score = parsed_response.get("quality_score")
        else:
            attempt_record = GenerationAttempt(
                id=attempt_id,
                slide_id=slide.id,
                attempt_number=attempt,
                prompt=prompt,
                response=response,
                quality_score=parsed_response.get("quality_score"),
            )
            self.db.add(attempt_record)
        self.db.commit()

    def _load_existing_slide_output(self, ppt_job_id: str, slide_number: int) -> Dict[str, Any]:
        llmout_file = self.llmout_dir / f"{ppt_job_id.replace('_', '-')}.json"
        if llmout_file.exists():
            data = load_json_safe(llmout_file)
            if data:
                for s in data.get("slides", []):
                    if s.get("slide_number") == slide_number:
                        return s
        return {
            "slide_number": slide_number,
            "title": "",
            "narration": "",
            "estimated_word_count": 0,
            "target_word_range": self.target_word_range,
            "quality_score": None,
            "quality_feedback": None,
            "generation_attempt": 1,
        }

    def _save_llm_output(self, ppt_job_id: str, parsed_data: Dict[str, Any], slides_output: List[Dict[str, Any]]):
        output_file = self.llmout_dir / f"{ppt_job_id.replace('_', '-')}.json"
        output_data = {
            "ppt_name": parsed_data["ppt"]["filename"],
            "generation_config": {
                "quality": self.quality,
                "length": self.length,
                "target_word_range": self.target_word_range,
            },
            "slides": slides_output,
        }
        save_json(output_file, output_data)
        logger.info(f"[{self.session_id}] Saved LLM output to {output_file}")

    def close(self):
        self.db.close()


def generate_content_for_session(session_id: str, quality: str, length: str) -> bool:
    from orchestrator import SessionManager
    from models.database import SessionLocal
    from models.models import PPTJob

    db = SessionLocal()
    jobs = db.query(PPTJob).filter(PPTJob.session_id == session_id).all()
    db.close()

    if not jobs:
        logger.warning(f"[{session_id}] No PPT jobs found for session")
        return False

    generator = ContentGenerator(session_id, quality, length)

    success_count = 0
    for job in jobs:
        if job.status.value != "completed":
            logger.info(f"[{session_id}] Skipping job {job.id} - status: {job.status.value}")
            continue

        logger.info(f"[{session_id}] Generating content for {job.ppt_filename}")
        if generator.generate_for_job(job.id):
            success_count += 1

    generator.close()
    logger.info(f"[{session_id}] Content generation complete. Success: {success_count}/{len(jobs)}")
    return success_count > 0