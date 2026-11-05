"""
Integration Tests for Alembic Migration Integrity
Verifies static migration script generation and schema completeness.
"""
import os

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command


@pytest.mark.integration
def test_alembic_migration_head_revision_resolves():
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    ini_path = os.path.join(backend_dir, "alembic.ini")

    alembic_cfg = Config(ini_path)
    alembic_cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))

    script_dir = ScriptDirectory.from_config(alembic_cfg)
    head_revision = script_dir.get_current_head()

    assert head_revision == "20261105_0007"


@pytest.mark.integration
def test_alembic_offline_sql_generation(capsys):
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    ini_path = os.path.join(backend_dir, "alembic.ini")

    alembic_cfg = Config(ini_path)
    alembic_cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))

    # Run upgrade head in offline sql mode
    command.upgrade(alembic_cfg, "head", sql=True)
    captured = capsys.readouterr()

    assert "CREATE TABLE prediction_snapshots" in captured.out
    assert "CREATE TABLE feature_snapshots" in captured.out
    assert "CREATE TABLE model_versions" in captured.out
    assert "CREATE TABLE historical_dataset_releases" in captured.out
    assert "CREATE TABLE shadow_predictions" in captured.out
    assert "CREATE TABLE shadow_evaluations" in captured.out
    assert "CREATE TABLE monitoring_alerts" in captured.out
    assert "CREATE TABLE scheduler_leases" in captured.out
    assert "CREATE TABLE operational_runs" in captured.out
    assert "CREATE TABLE operational_failures" in captured.out
    assert "20261023_0006" in captured.out
