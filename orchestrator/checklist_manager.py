import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from models.models import PPTJob, PPTJobStatus, Slide, SlideStatus, VideoJob, VideoStatus
from models.database import SessionLocal
from config.settings import settings
from utils.json_utils import save_json, load_json_safe
from utils.filesystem import ensure_dir
from utils.logging import get_logger


logger = get_logger("checklist_manager")


class ChecklistManager:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.checklist_dir = settings.PROCESSING_DIR / "checklist" / session_id
        self.checklist_path = self.checklist_dir / "checklist.json"
        ensure_dir(self.checklist_dir)
        self.db = SessionLocal()

    def close(self):
        self.db.close()

    def create_checklist(self, jobs: List[Tuple[str, str]]) -> Dict[str, Any]:
        checklist = {
            "session_id": self.session_id,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "jobs": [],
        }

        for job_id, filename in jobs:
            checklist["jobs"].append({
                "ppt_id": job_id,
                "file": filename,
                "status": "pending",
                "parse_status": "pending",
                "content_status": "pending",
                "quality_status": "pending",
                "tts_status": "pending",
                "video_status": "pending",
            })

        save_json(self.checklist_path, checklist)
        logger.info(f"[{self.session_id}] Created checklist with {len(checklist['jobs'])} jobs")
        return checklist

    def load_checklist(self) -> Optional[Dict[str, Any]]:
        return load_json_safe(self.checklist_path)

    def update_job_status(self, ppt_id: str, status: str, **stage_statuses) -> bool:
        checklist = self.load_checklist()
        if not checklist:
            return False

        for job in checklist["jobs"]:
            if job["ppt_id"] == ppt_id:
                job["status"] = status
                for key, value in stage_statuses.items():
                    if key in job:
                        job[key] = value
                break
        else:
            return False

        checklist["updated_at"] = datetime.utcnow().isoformat()
        save_json(self.checklist_path, checklist)
        return True

    def update_parse_status(self, ppt_id: str, parse_status: str) -> bool:
        return self.update_job_status(ppt_id, parse_status, parse_status=parse_status)

    def update_content_status(self, ppt_id: str, content_status: str) -> bool:
        return self.update_job_status(ppt_id, content_status, content_status=content_status)

    def update_quality_status(self, ppt_id: str, quality_status: str) -> bool:
        return self.update_job_status(ppt_id, quality_status, quality_status=quality_status)

    def update_tts_status(self, ppt_id: str, tts_status: str) -> bool:
        return self.update_job_status(ppt_id, tts_status, tts_status=tts_status)

    def update_video_status(self, ppt_id: str, video_status: str) -> bool:
        return self.update_job_status(ppt_id, video_status, video_status=video_status)

    def get_pending_jobs(self) -> List[Dict[str, Any]]:
        checklist = self.load_checklist()
        if not checklist:
            return []
        return [j for j in checklist["jobs"] if j["parse_status"] != "completed"]

    def get_completed_jobs(self) -> List[Dict[str, Any]]:
        checklist = self.load_checklist()
        if not checklist:
            return []
        return [j for j in checklist["jobs"] if j["parse_status"] == "completed"]

    def sync_from_database(self, ppt_jobs: List[PPTJob]) -> Dict[str, Any]:
        checklist = self.load_checklist()
        if not checklist:
            checklist = {"session_id": self.session_id, "jobs": []}

        existing_jobs = {j["ppt_id"]: j for j in checklist["jobs"]}

        for job in ppt_jobs:
            job_dict = {
                "ppt_id": job.id,
                "file": job.ppt_filename,
                "status": job.status.value,
                "parse_status": "completed" if job.status == PPTJobStatus.COMPLETED else "pending",
                "content_status": "pending",
                "quality_status": "pending",
                "tts_status": "pending",
                "video_status": "pending",
            }

            slides = job.slides
            if slides:
                parsed_count = sum(1 for s in slides if s.parsed)
                job_dict["parse_status"] = "completed" if parsed_count == len(slides) else ("in_progress" if parsed_count > 0 else "pending")

                content_generated_count = sum(1 for s in slides if s.content_generated)
                job_dict["content_status"] = "completed" if content_generated_count == len(slides) else ("in_progress" if content_generated_count > 0 else "pending")

                quality_checked_count = sum(1 for s in slides if s.status == SlideStatus.QUALITY_CHECKED)
                job_dict["quality_status"] = "completed" if quality_checked_count == len(slides) else ("in_progress" if quality_checked_count > 0 else "pending")

                tts_completed_count = sum(1 for s in slides if s.status == SlideStatus.TTS_COMPLETED)
                job_dict["tts_status"] = "completed" if tts_completed_count == len(slides) else ("in_progress" if tts_completed_count > 0 else "pending")

                video_job = self.db.query(VideoJob).filter(VideoJob.ppt_job_id == job.id).first()
                if video_job:
                    job_dict["video_status"] = video_job.status.value
                else:
                    job_dict["video_status"] = "pending"

            if job.id in existing_jobs:
                existing_jobs[job.id].update(job_dict)
            else:
                checklist["jobs"].append(job_dict)

        checklist["updated_at"] = datetime.utcnow().isoformat()
        save_json(self.checklist_path, checklist)
        return checklist