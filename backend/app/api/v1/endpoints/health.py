from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db

router = APIRouter()


class SystemHealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    version: str
    database: str


@router.get("/health", response_model=SystemHealthResponse)
def get_system_health(db: Session = Depends(get_db)):
    """
    System Health Check Endpoint.
    Verifies FastAPI operational status and database connectivity.
    """
    db_status = "unreachable"
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected" if db.get_bind().dialect.name == "postgresql" else "development_sqlite"
    except Exception:
        db_status = "disconnected"

    return SystemHealthResponse(
        status="ok" if db_status == "connected" else "degraded",
        service=settings.APP_NAME,
        environment=settings.APP_ENV,
        version="0.1.0",
        database=db_status,
    )
