from fastapi import APIRouter

from app.api.v1.endpoints import (
    evaluations,
    health,
    matches,
    models,
    operations,
    predictions,
)

api_v1_router = APIRouter()

api_v1_router.include_router(health.router, tags=["system"])
api_v1_router.include_router(matches.router, prefix="/matches", tags=["matches"])
api_v1_router.include_router(predictions.router, prefix="/predictions", tags=["predictions"])
api_v1_router.include_router(models.router, prefix="/models", tags=["models"])
api_v1_router.include_router(evaluations.router, prefix="/evaluations", tags=["evaluations"])

api_v1_router.include_router(operations.router, tags=["operations"])
