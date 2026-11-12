"""No unauthorized network requests or fabricated source receipts."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.research import historical_oos as history
from app.research import football_data_result_verifier as results
from app.research.source_policy import APPROVAL_ENV, require_football_data_network_permission


def test_restricted_source_downloads_blocked_before_network(tmp_path, monkeypatch):
    monkeypatch.delenv(APPROVAL_ENV, raising=False)
    def fail(*args, **kwargs):
        pytest.fail("Network must not be contacted before approval")
    monkeypatch.setattr(history, "urlopen", fail)
    monkeypatch.setattr(results, "urlopen", fail)
    with pytest.raises(RuntimeError, match="FOOTBALL_DATA_NETWORK_ACCESS_DISABLED"):
        history.download_completed_seasons(tmp_path / "history")
    with pytest.raises(RuntimeError, match="FOOTBALL_DATA_NETWORK_ACCESS_DISABLED"):
        results._read_url(f"{results.SOURCE_BASE}/E0.csv")
    with pytest.raises(RuntimeError, match="FOOTBALL_DATA_NETWORK_ACCESS_DISABLED"):
        require_football_data_network_permission()


def test_authorized_local_csv_reader_has_no_network_or_arbitrary_paths(tmp_path, monkeypatch):
    monkeypatch.delenv(APPROVAL_ENV, raising=False)
    csv = b"Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
    (tmp_path/"E0.csv").write_bytes(csv)
    reader = results.local_result_reader(tmp_path)
    assert reader(f"{results.SOURCE_BASE}/E0.csv")==csv
    with pytest.raises(ValueError, match="UNAPPROVED_LOCAL_RESULT_SOURCE"):
        reader("https://example.org/not-allowed.csv")
    with pytest.raises(ValueError, match="MISSING_OR_UNSAFE_LOCAL_RESULT_SNAPSHOT"):
        reader(f"{results.SOURCE_BASE}/SP1.csv")


def test_empty_or_whitespace_permission_rejected(monkeypatch):
    monkeypatch.setenv(APPROVAL_ENV, "  ")
    with pytest.raises(RuntimeError, match="FOOTBALL_DATA_NETWORK_ACCESS_DISABLED"):
        require_football_data_network_permission()
    monkeypatch.setenv(APPROVAL_ENV, "documented-permission-test-only")
    require_football_data_network_permission()
