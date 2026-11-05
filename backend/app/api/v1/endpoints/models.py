"""Read-only metadata for the frozen champion and structural fallback."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1.security import require_customer_api_token
from app.prediction.frozen_inference import FrozenArtifactInference

router = APIRouter(dependencies=[Depends(require_customer_api_token)])


@router.get("/")
def list_models():
    inference = FrozenArtifactInference()
    return {
        "models": [
            {
                "key": inference.c1.artifact.model_key,
                "version": inference.c1.artifact.version,
                "role": "PRIMARY_1X2",
                "fingerprint": inference.c1.artifact.artifact_fingerprint,
                "file_sha256": inference.c1_file_sha256,
            },
            {
                "key": inference.m1.artifact.model_key,
                "version": inference.m1.artifact.version,
                "role": "GOALS_AND_FALLBACK",
                "fingerprint": inference.m1.artifact.artifact_fingerprint,
                "file_sha256": inference.m1_file_sha256,
            },
        ],
        "status": "FROZEN_ARTIFACTS_VERIFIED",
        "note": "Artifact verification does not establish live-data or deployment certification.",
    }
