"""Customer API authorization: deny access until explicitly configured."""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def require_customer_api_token(
    x_footmo_api_key: str | None = Header(default=None),
) -> None:
    configured = settings.CUSTOMER_API_TOKEN
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Customer API is not enabled.",
        )
    if not x_footmo_api_key or not hmac.compare_digest(
        x_footmo_api_key, configured
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid customer API key required.",
        )
