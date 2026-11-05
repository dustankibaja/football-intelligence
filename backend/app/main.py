from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.exceptions import FootballIntelligenceException
from app.core.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup and shutdown events lifecycle context.
    """
    logger.info(
        f"Starting {settings.APP_NAME} in [{settings.APP_ENV}] mode"
    )
    yield
    logger.info(
        f"Shutting down {settings.APP_NAME}"
    )


def create_application() -> FastAPI:
    """
    FastAPI Application Factory.
    """
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="Scientific Football Intelligence & Probability Analysis System API",
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    # No wildcard CORS: explicit browser origins only.
    origins = [o.strip() for o in settings.CUSTOMER_ALLOWED_ORIGINS.split(",") if o.strip()]
    if origins and "*" not in origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET"],
            allow_headers=["X-Footmo-API-Key", "Accept"],
        )

    # Register API v1 Router
    app.include_router(api_v1_router, prefix="/api/v1")

    # Custom Exception Handlers
    @app.exception_handler(FootballIntelligenceException)
    async def custom_exception_handler(request: Request, exc: FootballIntelligenceException):
        logger.error(f"Application error [{exc.code}]: {exc.message}")
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                }
            },
        )

    return app


app = create_application()
