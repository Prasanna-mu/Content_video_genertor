import os
import subprocess
import imageio_ffmpeg
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from models.database import SessionLocal
from models.models import Slide, SlideStatus, AudioAsset, AudioStatus, VideoJob, VideoStatus, PPTJob
from config.settings import settings
from utils.json_utils import load_json_safe, save_json
from utils.filesystem import ensure_dir
from utils.logging import get_logger
from orchestrator.slide_converter import SlideConverter, validate_slide_image, SlideImageResult


logger = get_logger("video_renderer")


class VideoRenderer:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.db = SessionLocal()
        self.delivery_dir = settings.DELIVERY_DIR / session_id
        self.audio_dir = settings.PROCESSING_DIR / "audio" / session_id
        self.llmout_dir = settings.PROCESSING_DIR / "llmout" / session_id
        self.parse_dir = settings.PROCESSING_DIR / "parseppt" / session_id
        self.segments_dir = self.delivery_dir / "segments"
        self.file_list_path = self.delivery_dir / "file_list.txt"
        ensure_dir(self.delivery_dir)
        ensure_dir(self.segments_dir)

        try:
            self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            logger.info(f"[{self.session_id}] Using FFmpeg from: {self.ffmpeg_exe}")
        except Exception as e:
            logger.error(f"[{self.session_id}] Failed to get FFmpeg executable: {e}")
            self.ffmpeg_exe = 'ffmpeg'

        self.slide_converter = SlideConverter(session_id)

    def render_video_for_job(self, ppt_job_id: str) -> bool:
        job = self.db.query(PPTJob).filter(PPTJob.id == ppt_job_id).first()
        if not job:
            logger.error(f"[{self.session_id}] PPT job not found: {ppt_job_id}")
            return False

        existing_video = self.db.query(VideoJob).filter(VideoJob.ppt_job_id == ppt_job_id).first()
        if existing_video and existing_video.status == VideoStatus.COMPLETED:
            logger.info(f"[{self.session_id}] Video already completed for job {ppt_job_id}, skipping")
            return True

        if existing_video:
            existing_video.status = VideoStatus.RENDERING
            existing_video.started_at = datetime.utcnow()
        else:
            existing_video = VideoJob(
                id=f"{ppt_job_id}_video",
                ppt_job_id=ppt_job_id,
                status=VideoStatus.RENDERING,
                started_at=datetime.utcnow()
            )
            self.db.add(existing_video)
        self.db.commit()

        try:
            slides = self.db.query(Slide).filter(
                Slide.ppt_job_id == ppt_job_id
            ).order_by(Slide.slide_number).all()

            if not slides:
                logger.error(f"[{self.session_id}] No slides found for job: {ppt_job_id}")
                raise Exception("No slides found")

            self._ensure_slide_images_converted(job, slides)

            segment_files = []
            total_duration = 0

            for slide in slides:
                audio_asset = self.db.query(AudioAsset).filter(
                    AudioAsset.slide_id == slide.id,
                    AudioAsset.status == AudioStatus.GENERATED
                ).first()

                if not audio_asset:
                    logger.warning(f"[{self.session_id}] No audio found for slide {slide.slide_number}")
                    continue

                if not os.path.exists(audio_asset.file_path):
                    logger.warning(f"[{self.session_id}] Audio file not found: {audio_asset.file_path}")
                    continue

                if not slide.image_path or not validate_slide_image(slide.image_path, self.session_id):
                    logger.warning(f"[{self.session_id}] Slide image not available or invalid for slide {slide.slide_number}")
                    continue

                segment_path = self._create_video_segment(
                    slide.image_path,
                    audio_asset.file_path,
                    audio_asset.duration,
                    slide.slide_number
                )
                
                if segment_path:
                    if self._validate_segment(segment_path):
                        segment_files.append(segment_path)
                        total_duration += audio_asset.duration
                    else:
                        logger.error(f"[{self.session_id}] Segment validation failed for slide {slide.slide_number}")
                else:
                    logger.error(f"[{self.session_id}] Failed to create segment for slide {slide.slide_number}")

            if not segment_files:
                logger.error(f"[{self.session_id}] No valid video segments created for job {ppt_job_id}")
                raise Exception("No valid video segments created")

            final_video_path = self.delivery_dir / f"{Path(job.ppt_filename).stem}.mp4"
            
            if self._combine_segments(segment_files, final_video_path):
                if self._validate_final_video(final_video_path):
                    existing_video.status = VideoStatus.COMPLETED
                    existing_video.completed_at = datetime.utcnow()
                    existing_video.output_path = str(final_video_path)
                    existing_video.duration = int(total_duration)
                    self.db.commit()

                    self._update_checklist_video_status(ppt_job_id, "completed")

                    logger.info(f"[{self.session_id}] Video rendering completed: {final_video_path} (duration: {total_duration:.1f}s)")
                    return True
                else:
                    logger.error(f"[{self.session_id}] Final video validation failed")
                    raise Exception("Final video validation failed")
            else:
                logger.error(f"[{self.session_id}] Failed to combine video segments for job {ppt_job_id}")
                raise Exception("Failed to combine segments")

        except Exception as e:
            logger.error(f"[{self.session_id}] Video rendering failed for job {ppt_job_id}: {e}")
            if existing_video:
                existing_video.status = VideoStatus.FAILED
                existing_video.completed_at = datetime.utcnow()
                existing_video.error = str(e)
                self.db.commit()
            self._update_checklist_video_status(ppt_job_id, "failed")
            return False

    def _ensure_slide_images_converted(self, job: PPTJob, slides: List[Slide]):
        ppt_path = settings.INPUT_PPT_DIR / job.ppt_filename
        if not ppt_path.exists():
            logger.error(f"[{self.session_id}] PPT file not found for conversion: {ppt_path}")
            return

        expected_slide_count = len(slides)
        logger.info(f"[{self.session_id}] Converting PPT slides to images for {job.ppt_filename}")

        results = self.slide_converter.convert_missing_slides(ppt_path, job.id, expected_slide_count)

        for result in results:
            slide = next((s for s in slides if s.slide_number == result.slide_number), None)
            if slide:
                if result.success:
                    slide.image_path = result.image_path
                    slide.image_width = result.width
                    slide.image_height = result.height
                    slide.image_status = "converted"
                else:
                    slide.image_status = "failed"
                    logger.error(f"[{self.session_id}] Slide {result.slide_number} conversion failed: {result.error}")

        self.db.commit()

    def _create_video_segment(
        self,
        image_path: str,
        audio_path: str,
        duration: int,
        slide_number: int
    ) -> Optional[str]:
        segment_path = self.segments_dir / f"slide_{slide_number:03d}.mp4"

        if segment_path.exists() and self._validate_segment(str(segment_path)):
            logger.info(f"[{self.session_id}] Segment already exists and valid: {segment_path}")
            return str(segment_path)

        try:
            from PIL import Image
            with Image.open(image_path) as img:
                img_width, img_height = img.size

            scale_filter = self._get_scale_filter(img_width, img_height)

            cmd = [
                self.ffmpeg_exe,
                '-y',
                '-loop', '1',
                '-framerate', '30',
                '-i', image_path,
                '-i', audio_path,
                '-c:v', 'libx264',
                '-t', str(duration),
                '-pix_fmt', 'yuv420p',
                '-vf', scale_filter,
                '-c:a', 'aac',
                '-b:a', '192k',
                '-shortest',
                str(segment_path)
            ]

            logger.debug(f"[{self.session_id}] FFmpeg segment command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if result.returncode != 0:
                logger.error(f"[{self.session_id}] FFmpeg segment failed: {result.stderr}")
                return None

            return str(segment_path) if segment_path.exists() else None

        except subprocess.TimeoutExpired:
            logger.error(f"[{self.session_id}] FFmpeg segment timed out for slide {slide_number}")
            return None
        except Exception as e:
            logger.error(f"[{self.session_id}] Failed to create video segment for slide {slide_number}: {e}")
            return None

    def _get_scale_filter(self, img_width: int, img_height: int) -> str:
        target_width = 1920
        target_height = 1080

        img_aspect = img_width / img_height
        target_aspect = target_width / target_height

        if abs(img_aspect - target_aspect) < 0.01:
            return f"scale={target_width}:{target_height}"

        if img_aspect > target_aspect:
            scaled_width = target_width
            scaled_height = int(target_width / img_aspect)
            if scaled_height % 2 != 0:
                scaled_height += 1
            return f"scale={scaled_width}:{scaled_height},pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black"
        else:
            scaled_height = target_height
            scaled_width = int(target_height * img_aspect)
            if scaled_width % 2 != 0:
                scaled_width += 1
            return f"scale={scaled_width}:{scaled_height},pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black"

    def _combine_segments(self, segment_files: List[str], output_path: Path) -> bool:
        try:
            if not segment_files:
                return False

            with open(self.file_list_path, 'w', encoding='utf-8') as f:
                for segment_file in segment_files:
                    # Use absolute paths for concat demuxer with -safe 0
                    abs_path = Path(segment_file).resolve()
                    f.write(f"file '{abs_path}'\n")

            cmd = [
                self.ffmpeg_exe,
                '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(self.file_list_path),
                '-c', 'copy',
                str(output_path)
            ]

            logger.debug(f"[{self.session_id}] FFmpeg concat command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if self.file_list_path.exists():
                self.file_list_path.unlink()

            if result.returncode != 0:
                logger.error(f"[{self.session_id}] FFmpeg concat failed: {result.stderr}")
                return False

            return output_path.exists() and output_path.stat().st_size > 0

        except subprocess.TimeoutExpired:
            logger.error(f"[{self.session_id}] FFmpeg concat timed out")
            return False
        except Exception as e:
            logger.error(f"[{self.session_id}] Failed to combine segments: {e}")
            return False

    def _validate_segment(self, segment_path: str) -> bool:
        try:
            path = Path(segment_path)
            if not path.exists():
                return False
            if path.stat().st_size == 0:
                return False

            cmd = [
                self.ffmpeg_exe,
                '-v', 'error',
                '-i', segment_path,
                '-f', 'null',
                '-'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode == 0

        except Exception as e:
            logger.warning(f"[{self.session_id}] Segment validation error: {e}")
            return False

    def _validate_final_video(self, video_path: Path) -> bool:
        try:
            if not video_path.exists():
                return False
            if video_path.stat().st_size == 0:
                return False

            cmd = [
                self.ffmpeg_exe,
                '-v', 'error',
                '-i', str(video_path),
                '-f', 'null',
                '-'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                logger.error(f"[{self.session_id}] Final video validation failed: {result.stderr}")
                return False

            probe_cmd = [
                self.ffmpeg_exe,
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height,duration',
                '-of', 'csv=p=0',
                str(video_path)
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
            if probe_result.returncode == 0 and probe_result.stdout.strip():
                parts = probe_result.stdout.strip().split(',')
                if len(parts) >= 3:
                    width, height, duration = parts[0], parts[1], parts[2]
                    logger.info(f"[{self.session_id}] Final video: {width}x{height}, duration: {duration}s")
                    return True

            return True

        except Exception as e:
            logger.error(f"[{self.session_id}] Final video validation error: {e}")
            return False

    def _update_checklist_video_status(self, ppt_job_id: str, status: str):
        try:
            from orchestrator.checklist_manager import ChecklistManager
            checklist_manager = ChecklistManager(self.session_id)
            checklist_manager.update_job_status(ppt_job_id, status, video_status=status)
        except Exception as e:
            logger.warning(f"[{self.session_id}] Failed to update checklist video status: {e}")

    def close(self):
        self.db.close()


def render_videos_for_session(session_id: str) -> bool:
    from models.database import SessionLocal
    from models.models import PPTJob, PPTJobStatus

    db = SessionLocal()
    jobs = db.query(PPTJob).filter(
        PPTJob.session_id == session_id,
        PPTJob.status == PPTJobStatus.COMPLETED
    ).all()
    db.close()

    if not jobs:
        logger.warning(f"[{session_id}] No completed PPT jobs found for video rendering")
        return False

    renderer = VideoRenderer(session_id)
    success_count = 0

    for job in jobs:
        logger.info(f"[{session_id}] Rendering video for {job.ppt_filename}")
        if renderer.render_video_for_job(job.id):
            success_count += 1

    renderer.close()
    logger.info(f"[{session_id}] Video rendering complete. Success: {success_count}/{len(jobs)}")
    return success_count > 0