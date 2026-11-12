"""Private registry metadata without exposing model artifacts or credentials."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.v1.research_access import internal_research_read, page
from app.db.models import ModelVersion
from app.db.session import get_db

router = APIRouter(dependencies=[Depends(internal_research_read)])


@router.get("/")
def list_models(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    db: Session = Depends(get_db),
):
    query = select(ModelVersion).options(joinedload(ModelVersion.model))
    rows = db.scalars(query.order_by(ModelVersion.created_at.desc(), ModelVersion.id).offset(offset).limit(limit)).all()
    return page([{
        "id": str(row.id), "model_key": row.model.model_key, "name": row.model.name,
        "family": row.model.model_family, "version": row.version,
        "lifecycle_status": row.lifecycle_status,
        "training_start_date": row.training_start_date,
        "training_end_date": row.training_end_date,
        "publication_eligible": False,
    } for row in rows], limit, offset)
