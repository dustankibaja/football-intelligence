"""Fail-closed server-only authorization for private research reads."""
from secrets import compare_digest

from fastapi import Header, HTTPException, Response

from app.core.config import settings


def internal_research_read(
    response: Response,
    authorization: str | None = Header(None),
) -> None:
    """This is an internal operator interface, NOT publication authorization."""
    configured = settings.INTERNAL_RESEARCH_READ_TOKEN
    expected = configured.get_secret_value() if configured else ""
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="Private research reads are disabled.")
    scheme, separator, token = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer" or not separator
        or not compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
    ):
        raise HTTPException(
            status_code=401, detail="Private research authorization required.",
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        )
    response.headers["Cache-Control"] = "no-store"


def page(items: list[dict], limit: int, offset: int) -> dict:
    return {
        "scope": "PRIVATE_RESEARCH_READ_ONLY",
        "publication_eligible": False,
        "items": items, "returned": len(items), "limit": limit, "offset": offset,
    }
