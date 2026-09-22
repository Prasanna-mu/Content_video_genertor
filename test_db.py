import sys
sys.path.insert(0, r'C:\Users\User\Desktop\Projects\Course Video Creator')

from models.database import SessionLocal, init_db
from models.models import Slide, PPTJob

init_db()
db = SessionLocal()
slides = db.query(Slide).all()
print(f'Total slides: {len(slides)}')
jobs = db.query(PPTJob).all()
print(f'Total jobs: {len(jobs)}')
for job in jobs:
    print(f'  Job: {job.id}, {job.ppt_filename}, status: {job.status}')