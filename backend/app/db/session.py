from collections.abc import Generator
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

from pathlib import Path
from sqlalchemy import text

from sqlalchemy import event

def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000") # 15 seconds
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

def _init_engine():
    # A production or CI database failure must not silently become a new local
    # SQLite database. Only explicitly local development/test may fall back.
    strict_db = (
        settings.APP_ENV.lower() not in {"development", "test"}
        or os.environ.get("RUNNING_IN_CI", "").lower() in {"1", "true", "yes"}
    )
    try:
        eng = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
        if strict_db and eng.dialect.name != "postgresql":
            raise RuntimeError("POSTGRESQL_REQUIRED")
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return eng
    except Exception:
        if strict_db:
            raise RuntimeError("PostgreSQL is required and unavailable; refusing SQLite fallback.") from None
        db_path = (Path(__file__).resolve().parent.parent.parent / "football_dev.db").resolve()
        fallback_url = f"sqlite:///{db_path}"
        eng = create_engine(
            fallback_url,
            connect_args={"check_same_thread": False},
        )
        
        event.listen(eng, "connect", set_sqlite_pragma)
        
        from app.db.models import Base
        Base.metadata.create_all(bind=eng)
        return eng

engine = _init_engine()

# Session Factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency yielding a transactional SQLAlchemy database session.
    Automatically closes session after HTTP request completion.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
