"""Engine/session setup. SQLite for V0; swapping DATABASE_URL to a Postgres DSN
is the only change needed to migrate later (plus adding Alembic migrations)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        _engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return _SessionLocal


def init_db() -> None:
    """Create all tables if they don't already exist. Idempotent."""
    Base.metadata.create_all(bind=get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope for a series of operations."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
