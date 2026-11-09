"""Prove that the CI database fixture uses PostgreSQL, not the local SQLite fallback."""

import os

import pytest
from sqlalchemy import text

from app.core.config import settings


@pytest.mark.integration
def test_ci_integration_database_is_postgresql(db_engine):
    if os.environ.get("RUNNING_IN_CI", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("CI PostgreSQL dialect enforcement runs on GitHub-hosted CI only")

    assert db_engine.dialect.name == "postgresql"
    assert settings.POSTGRES_SERVER in {"localhost", "127.0.0.1"}
    assert settings.POSTGRES_DB == "football_intelligence_test"
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT current_database()")).scalar_one() == (
            "football_intelligence_test"
        )
        assert connection.dialect.name == "postgresql"
