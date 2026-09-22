import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from tts.factory import get_tts_provider
from tts.base import TTSProvider, TTSResult
from models.database import SessionLocal
from models.models import Slide, SlideStatus, AudioAsset, AudioStatus
from config.settings import settings
from utils.json_utils import save_json, load_json_safe
from utils.filesystem import ensure_dir
from utils.logging import get_logger


logger = get_logger("tts_manager")


class TTSManager:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.db = SessionLocal()
        self.tts_provider = get_tts_provider()
        self.audio_dir = settings.PROCESSING_DIR / "audio" / session_id

    def generate_audio_for_job(self, ppt_job_id: str) -> bool:
        slides = self.db.query(Slide).filter(
            Slide.ppt_job_id == ppt_job_id
        ).order_by(Slide.slide_number).all()

        if not slides:
            logger.error(f"[{self.session_id}] No slides found for job: {ppt_job_id}")
            return False

        # Get PPT job to determine output directory name
        ppt_job = slides[0].ppt_job
        ppt_name = Path(ppt_job.ppt_filename).stem
        job_audio_dir = self.audio_dir / ppt_name
        ensure_dir(job_audio_dir)

        llmout_dir = settings.PROCESSING_DIR / "llmout" / self.session_id
        llmout_file = llmout_dir / f"{ppt_job_id.replace('_', '-')}.json"

        if not llmout_file.exists():
            logger.error(f"[{self.session_id}] LLM output not found: {llmout_file}")
            return False

        llm_data = load_json_safe(llmout_file)
        if not llm_data:
            logger.error(f"[{self.session_id}] Failed to load LLM output: {llmout_file}")
            return False

        slides_output = llm_data.get("slides", [])
        slide_outputs_by_number = {s["slide_number"]: s for s in slides_output}

        all_audio_manifest = []

        for slide in slides:
            # Check if already completed
            if slide.status == SlideStatus.TTS_COMPLETED:
                logger.info(f"[{self.session_id}] Slide {slide.slide_number} already has TTS, skipping")
                existing_audio = self._load_existing_audio(slide.id)
                if existing_audio:
                    all_audio_manifest.append(existing_audio)
                continue

            # Get narration from LLM output
            slide_output = slide_outputs_by_number.get(slide.slide_number)
            if not slide_output or not slide_output.get("narration"):
                logger.error(f"[{self.session_id}] No narration for slide {slide.slide_number}")
                continue

            narration = slide_output["narration"]
            
            # Generate audio file
            audio_filename = f"slide_{slide.slide_number:03d}.wav"
            audio_path = job_audio_dir / audio_filename

            logger.info(f"[{self.session_id}] Generating TTS for slide {slide.slide_number}")
            result = self.tts_provider.synthesize(narration, str(audio_path))

            if not result.success:
                logger.error(f"[{self.session_id}] TTS failed for slide {slide.slide_number}: {result.error}")
                self._save_audio_asset(slide, str(audio_path), 0.0, AudioStatus.FAILED)
                continue

            duration = result.duration_seconds
            logger.info(f"[{self.session_id}] TTS generated for slide {slide.slide_number}: {duration:.1f}s")

            # Save audio asset to database
            self._save_audio_asset(slide, str(audio_path), duration, AudioStatus.GENERATED)

            # Update slide status
            slide.status = SlideStatus.TTS_COMPLETED
            self.db.commit()

            all_audio_manifest.append({
                "slide_number": slide.slide_number,
                "file_path": str(audio_path),
                "duration_seconds": duration,
                "format": "wav",
                "provider": self.tts_provider.provider_name,
            })

        # Save audio manifest
        manifest_path = job_audio_dir / "audio_manifest.json"
        manifest_data = {
            "ppt_name": ppt_job.ppt_filename,
            "session_id": self.session_id,
            "tts_provider": self.tts_provider.provider_name,
            "slides": all_audio_manifest,
            "total_duration_seconds": sum(s.get("duration_seconds", 0) for s in all_audio_manifest),
        }
        save_json(manifest_path, manifest_data)
        logger.info(f"[{self.session_id}] Saved audio manifest to {manifest_path}")

        return True

    def _save_audio_asset(
        self,
        slide: Slide,
        file_path: str,
        duration: float,
        status: AudioStatus
    ):
        asset_id = f"{slide.id}_audio_{uuid.uuid4().hex[:8]}"
        audio_asset = AudioAsset(
            id=asset_id,
            slide_id=slide.id,
            provider=self.tts_provider.provider_name,
            file_path=file_path,
            duration=int(duration),
            format="wav",
            status=status,
        )
        self.db.add(audio_asset)
        self.db.commit()

    def _load_existing_audio(self, slide_id: str) -> Optional[Dict[str, Any]]:
        audio_asset = self.db.query(AudioAsset).filter(
            AudioAsset.slide_id == slide_id,
            AudioAsset.status == AudioStatus.GENERATED
        ).first()
        if audio_asset:
            return {
                "slide_number": 0,  # Will be set by caller
                "file_path": audio_asset.file_path,
                "duration_seconds": audio_asset.duration,
                "format": audio_asset.format,
                "provider": audio_asset.provider,
            }
        return None

    def close(self):
        self.db.close()


def generate_tts_for_session(session_id: str) -> bool:
    from models.database import SessionLocal
    from models.models import PPTJob, PPTJobStatus

    db = SessionLocal()
    jobs = db.query(PPTJob).filter(
        PPTJob.session_id == session_id,
        PPTJob.status == PPTJobStatus.COMPLETED
    ).all()
    db.close()

    if not jobs:
        logger.warning(f"[{session_id}] No completed PPT jobs found for TTS")
        return False

    manager = TTSManager(session_id)
    success_count = 0

    for job in jobs:
        logger.info(f"[{session_id}] Generating TTS for {job.ppt_filename}")
        if manager.generate_audio_for_job(job.id):
            success_count += 1

    manager.close()
    logger.info(f"[{session_id}] TTS generation complete. Success: {success_count}/{len(jobs)}")
    return success_count > 0