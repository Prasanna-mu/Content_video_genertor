import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

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
import main  # to access process_ppts
from config.settings import settings
from models.database import init_db

def main_test():
    print("Initializing database...")
    init_db()
    
    print("Creating session...")
    session_manager = SessionManager()
    session = session_manager.create_session(quality="beginner", length="normal")
    session_id = session.id
    print(f"Session ID: {session_id}")
    
    session_manager.start_session(session_id)
    print("Session started.")
    
    print("Loading jobs...")
    jobs_data = load_jobs_from_json()
    print(f"Jobs data: {jobs_data}")
    if not jobs_data:
        print("No jobs found.")
        return
    
    enabled_jobs = [j for j in jobs_data if j.get("enabled", True)]
    print(f"Enabled jobs: {len(enabled_jobs)}")
    
    # Update session counts
    session_manager.update_session_counts(session_id, total=len(enabled_jobs))
    
    # Create PPT job records
    ppt_jobs = create_ppt_jobs(session_id, enabled_jobs)
    print(f"Created {len(ppt_jobs)} PPT job records")
    
    # Create checklist
    checklist_manager = ChecklistManager(session_id)
    job_tuples = [(j.id, j.ppt_filename) for j in ppt_jobs]
    checklist_manager.create_checklist(job_tuples)
    print("Checklist created.")
    
    # Process PPTs (parsing) using main's function
    print("\nProcessing PPTs (parsing)...")
    main.process_ppts(session_id, "beginner", "normal")
    print("Processing PPTs phase complete.")
    
    # Content generation
    print("\nStarting content generation phase...")
    generate_content_for_session(session_id, "beginner", "normal")
    print("Content generation phase complete.")
    
    # TTS generation
    print("\nStarting TTS generation phase...")
    generate_tts_for_session(session_id)
    print("TTS generation phase complete.")
    
    # Video rendering
    print("\nStarting video rendering phase...")
    render_videos_for_session(session_id)
    print("Video rendering phase complete.")
    
    print("\nAll phases done.")

if __name__ == "__main__":
    main_test()