import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session as SQLSession
from models.models import Session, SessionStatus
from models.database import SessionLocal
from config.settings import settings


class SessionManager:
    def __init__(self):
        self.db: SQLSession = SessionLocal()

    def create_session(self, quality: str, length: str) -> Session:
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        session = Session(
            id=session_id,
            quality=quality,
            length=length,
            llm_provider=settings.LLM_PROVIDER,
            llm_model=settings.OLLAMA_MODEL,
            status=SessionStatus.CREATED,
        )

        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def start_session(self, session_id: str) -> Optional[Session]:
        session = self.db.query(Session).filter(Session.id == session_id).first()
        if session:
            session.status = SessionStatus.IN_PROGRESS
            session.started_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(session)
        return session

    def complete_session(self, session_id: str) -> Optional[Session]:
        session = self.db.query(Session).filter(Session.id == session_id).first()
        if session:
            session.status = SessionStatus.COMPLETED
            session.completed_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(session)
        return session

    def fail_session(self, session_id: str, error: str = None) -> Optional[Session]:
        session = self.db.query(Session).filter(Session.id == session_id).first()
        if session:
            session.status = SessionStatus.FAILED
            session.completed_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(session)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        return self.db.query(Session).filter(Session.id == session_id).first()

    def update_session_counts(self, session_id: str, total: int = None, completed: int = None, failed: int = None):
        session = self.db.query(Session).filter(Session.id == session_id).first()
        if session:
            if total is not None:
                session.total_ppt_count = total
            if completed is not None:
                session.completed_ppt_count = completed
            if failed is not None:
                session.failed_ppt_count = failed
            self.db.commit()

    def close(self):
        self.db.close()