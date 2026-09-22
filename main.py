#!/usr/bin/env python3
import sys
from orchestrator import (
    SessionManager,
    PPTProcessor,
    load_jobs_from_json,
    create_ppt_jobs,
    ChecklistManager,
    generate_content_for_session,
    generate_tts_for_session,
    render_videos_for_session,
)
from models.database import init_db
from config.settings import settings
from utils.logging import get_logger


logger = get_logger("main")


def ask_quality() -> str:
    print("\nContent Quality:")
    print("1. Beginner")
    print("2. Intermediate")
    print("3. Technical")
    print()

    while True:
        choice = input("Select: ").strip()
        if choice in settings.QUALITY_LEVELS:
            return settings.QUALITY_LEVELS[choice]
        print("Invalid selection. Please enter 1, 2, or 3.")


def ask_length() -> str:
    print("\nVideo Length:")
    print("1. Normal (30-60 seconds per slide)")
    print("2. Intermediate (1-2 minutes per slide)")
    print("3. Long (2-3 minutes per slide)")
    print("4. Extreme (3-4 minutes per slide)")
    print()

    while True:
        choice = input("Select: ").strip()
        if choice in settings.LENGTH_LEVELS:
            return settings.LENGTH_LEVELS[choice]
        print("Invalid selection. Please enter 1, 2, 3, or 4.")


def process_ppts(session_id: str, quality: str, length: str):
    logger.info(f"[{session_id}] Loading jobs from data/jobs.json")
    jobs_data = load_jobs_from_json()

    if not jobs_data:
        logger.warning(f"[{session_id}] No jobs found in data/jobs.json")
        return

    enabled_jobs = [j for j in jobs_data if j.get("enabled", True)]
    logger.info(f"[{session_id}] Found {len(enabled_jobs)} enabled PPT jobs")

    session_manager = SessionManager()
    session_manager.update_session_counts(session_id, total=len(enabled_jobs))

    ppt_jobs = create_ppt_jobs(session_id, enabled_jobs)
    logger.info(f"[{session_id}] Created {len(ppt_jobs)} PPT job records")

    checklist_manager = ChecklistManager(session_id)
    job_tuples = [(j.id, j.ppt_filename) for j in ppt_jobs]
    checklist_manager.create_checklist(job_tuples)

    processor = PPTProcessor(session_id)

    completed_count = 0
    failed_count = 0

    for job in ppt_jobs:
        logger.info(f"[{session_id}] Processing PPT: {job.ppt_filename} ({job.id})")
        checklist_manager.update_parse_status(job.id, "in_progress")

        success = processor.process_job(job.id)

        if success:
            completed_count += 1
            checklist_manager.update_parse_status(job.id, "completed")
            logger.info(f"[{session_id}] Completed parsing: {job.ppt_filename}")
        else:
            failed_count += 1
            checklist_manager.update_parse_status(job.id, "failed")
            logger.error(f"[{session_id}] Failed parsing: {job.ppt_filename}")

        session_manager.update_session_counts(
            session_id,
            completed=completed_count,
            failed=failed_count
        )

    processor.close()
    session_manager.close()

    logger.info(f"[{session_id}] PPT parsing phase complete. Success: {completed_count}, Failed: {failed_count}")


def generate_content_phase(session_id: str, quality: str, length: str):
    logger.info(f"[{session_id}] Starting content generation phase")
    generate_content_for_session(session_id, quality, length)
    logger.info(f"[{session_id}] Content generation phase complete")


def tts_phase(session_id: str):
    logger.info(f"[{session_id}] Starting TTS generation phase")
    generate_tts_for_session(session_id)
    logger.info(f"[{session_id}] TTS generation phase complete")


def video_rendering_phase(session_id: str):
    logger.info(f"[{session_id}] Starting video rendering phase")
    render_videos_for_session(session_id)
    logger.info(f"[{session_id}] Video rendering phase complete")


def main():
    print("PPT-to-Video AI Agent")
    print("=====================")

    quality = ask_quality()
    length = ask_length()

    print(f"\nSelected Quality: {quality}")
    print(f"Selected Length: {length}")

    init_db()

    session_manager = SessionManager()
    session = session_manager.create_session(quality=quality, length=length)

    print(f"\n[SESSION] Created {session.id}")
    print(f"Quality: {session.quality}")
    print(f"Length: {session.length}")
    print(f"LLM Provider: {session.llm_provider}")
    print(f"LLM Model: {session.llm_model}")

    session_manager.start_session(session.id)
    print(f"[SESSION] Started {session.id}")

    process_ppts(session.id, quality, length)

    generate_content_phase(session.id, quality, length)

    tts_phase(session.id)

    video_rendering_phase(session.id)

    session_manager.complete_session(session.id)
    print(f"[SESSION] Completed {session.id}")

    print(f"\nPhase 7 complete - Video rendering done.")
    print(f"Session ID: {session.id}")


if __name__ == "__main__":
    main()