import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.models import Base as ModelsBase
from app.db.session import get_db
from app.main import app


@pytest.fixture(scope="session")
def db_engine():
    """Use real PostgreSQL in CI; retain SQLite fallback for offline local testing only."""
    ci_mode = os.environ.get("RUNNING_IN_CI", "").strip().lower() in {"1", "true", "yes"}

    if ci_mode:
        # Fail before connecting if a developer/production database could be selected.
        if (
            settings.POSTGRES_SERVER not in {"localhost", "127.0.0.1"}
            or settings.POSTGRES_DB != "football_intelligence_test"
            or settings.DATABASE_URL_OVERRIDE is not None
        ):
            pytest.fail(
                "CI database must be the disposable localhost football_intelligence_test "
                "PostgreSQL service, with no DATABASE_URL_OVERRIDE.",
                pytrace=False,
            )

        engine = create_engine(settings.database_url, pool_pre_ping=True)
        try:
            if engine.dialect.name != "postgresql":
                pytest.fail("CI integration engine must use PostgreSQL.", pytrace=False)
            # A PostgreSQL outage must fail CI, never silently switch to SQLite.
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            yield engine
        finally:
            engine.dispose()
        return

    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        # Developer convenience only: no local PostgreSQL means in-memory SQLite.
        fallback_engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        ModelsBase.metadata.create_all(bind=fallback_engine)
        try:
            yield fallback_engine
        finally:
            fallback_engine.dispose()
    else:
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine) -> Generator[Session, None, None]:
    """Function-scoped rollback-isolated session."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(autocommit=False, autoflush=False, bind=connection)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="function")
def client(db_session) -> Generator[TestClient, None, None]:
    """FastAPI client with transactional database dependency override."""
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
