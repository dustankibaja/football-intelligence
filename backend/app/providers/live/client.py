"""
Sprint 7.2 Live Football Data Provider Client
Robust HTTP client implementing authentication, quota tracking, clock skew detection,
error classification (transient vs permanent), odds filtering, and deterministic payload fingerprinting.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from app.automation.clock import Clock, SystemClock
from app.automation.errors import (
    ErrorCategory,
    OperationalException,
    PermanentProviderError,
    Retryability,
    TransientProviderError,
)
from app.core.config import settings
from app.providers.live.base import (
    LiveFixtureDTO,
    LiveFixtureStatus,
    LiveFootballDataProvider,
    LiveResultDTO,
    ProviderRateLimitState,
    compute_payload_fingerprint,
)
from app.providers.live.mapping import (
    OddsExclusionPolicy,
    ProviderCompetitionMapper,
    ProviderTeamMapper,
)

DEFAULT_BASE_URL = "https://api.football-data.org/v4"


class LiveFootballDataClient(LiveFootballDataProvider):
    """
    Certified client adapter for live football data provider APIs.
    Loads credentials securely, masks keys in diagnostics, and enforces temporal & quality gates.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        team_mapper: ProviderTeamMapper | None = None,
        clock: Clock | None = None,
        transport: Any = None,  # For unit testing / mock injection
    ) -> None:
        self._api_key = api_key or getattr(settings, "LIVE_DATA_API_KEY", None)
        self._base_url = (base_url or getattr(settings, "LIVE_DATA_API_BASE_URL", None) or DEFAULT_BASE_URL).rstrip("/")
        self.team_mapper = team_mapper or ProviderTeamMapper()
        self.clock = clock or SystemClock()
        self.transport = transport
        self._rate_limit_state = ProviderRateLimitState()
        self._clock_skew_seconds: float = 0.0

    @property
    def provider_name(self) -> str:
        return "football_data_org"

    @property
    def api_key(self) -> str | None:
        return self._api_key

    def __repr__(self) -> str:
        masked_key = "***" if self._api_key else "None"
        return f"LiveFootballDataClient(provider='{self.provider_name}', base_url='{self._base_url}', api_key='{masked_key}')"

    def __str__(self) -> str:
        return self.__repr__()

    def health_check(self) -> dict[str, Any]:
        """Perform non-destructive connectivity and authentication verification."""
        if not self._api_key:
            return {
                "status": "FAIL",
                "provider_configured": False,
                "error": "LIVE_PROVIDER_CONFIG_MISSING",
                "details": "API key is not configured.",
            }

        try:
            # Query status endpoint or competitions list
            data = self._make_request("/competitions")
            return {
                "status": "PASS",
                "provider_configured": True,
                "reachable": True,
                "competitions_available": len(data.get("competitions", [])),
                "clock_skew_seconds": self._clock_skew_seconds,
                "rate_limit_remaining": self._rate_limit_state.remaining,
            }
        except OperationalException as exc:
            return {
                "status": "FAIL",
                "provider_configured": True,
                "reachable": False,
                "error": exc.failure_code,
                "message": exc.message,
            }

    def get_upcoming_fixtures(
        self,
        competition_codes: list[str] | None = None,
        days_ahead: int = 7,
    ) -> list[LiveFixtureDTO]:
        """
        Fetch real upcoming fixtures for supported competitions.
        Filters out unsupported leagues, sanitizes odds, and maps team identities.
        """
        if not self._api_key and not self.transport:
            raise PermanentProviderError(
                "PROVIDER_AUTHENTICATION_MISSING",
                "LIVE_DATA_API_KEY is not configured.",
            )

        now_utc = self.clock.now()
        date_from = now_utc.strftime("%Y-%m-%d")
        date_to = (now_utc + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        path = f"/matches?dateFrom={date_from}&dateTo={date_to}"
        response_data = self._make_request(path)

        matches = response_data.get("matches", [])
        retrieved_at = self.clock.now()
        fixtures: list[LiveFixtureDTO] = []

        for m in matches:
            # 1. Sanitize Odds
            clean_m = OddsExclusionPolicy.sanitize_payload(m)
            fp = compute_payload_fingerprint(clean_m)

            # 2. Filter & Map Competition
            comp_info = clean_m.get("competition", {})
            comp_id = comp_info.get("id") or comp_info.get("code")
            if not comp_id or not ProviderCompetitionMapper.is_supported(comp_id):
                continue
            canonical_comp_code = ProviderCompetitionMapper.map_competition(comp_id)

            # 3. Parse Teams
            home_info = clean_m.get("homeTeam", {})
            away_info = clean_m.get("awayTeam", {})
            p_home_id = str(home_info.get("id", ""))
            p_home_name = home_info.get("name", "Unknown Home")
            p_away_id = str(away_info.get("id", ""))
            p_away_name = away_info.get("name", "Unknown Away")

            # 4. Map Canonical Teams (allow None for unresolved, but do not fuzzy guess)
            c_home_id = self.team_mapper.map_team(p_home_id, p_home_name, allow_unresolved=True)
            c_away_id = self.team_mapper.map_team(p_away_id, p_away_name, allow_unresolved=True)

            # 5. Parse Kickoff Time
            kickoff_str = clean_m.get("utcDate")
            if not kickoff_str:
                continue
            kickoff_utc = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00")).astimezone(UTC)

            # 6. Parse Status
            status_str = clean_m.get("status", "SCHEDULED").upper()
            try:
                status = LiveFixtureStatus(status_str)
            except ValueError:
                status = LiveFixtureStatus.SCHEDULED

            season_info = clean_m.get("season", {})
            season_year = str(season_info.get("startDate", "2026")[:4])

            fixtures.append(
                LiveFixtureDTO(
                    provider_fixture_id=str(clean_m.get("id")),
                    provider_name=self.provider_name,
                    provider_competition_id=str(comp_id),
                    competition_code=canonical_comp_code,
                    season_year=season_year,
                    provider_home_team_id=p_home_id,
                    provider_home_team_name=p_home_name,
                    provider_away_team_id=p_away_id,
                    provider_away_team_name=p_away_name,
                    canonical_home_team_id=c_home_id,
                    canonical_away_team_id=c_away_id,
                    kickoff_utc=kickoff_utc,
                    status=status,
                    retrieved_at_utc=retrieved_at,
                    payload_fingerprint=fp,
                    raw_payload=clean_m,
                )
            )

        return fixtures

    def get_recent_finished_matches(
        self, days_back: int = 7, window_days: int = 7,
    ) -> list[dict[str, Any]]:
        """Read actual recent final scores in bounded date windows.

        Intended for explicitly scheduled history synchronization, not a
        synthetic historical backfill. The provider's quota still applies.
        """
        from uuid import NAMESPACE_URL, uuid5

        if not self._api_key and not self.transport:
            raise PermanentProviderError(
                "PROVIDER_AUTHENTICATION_MISSING", "LIVE_DATA_API_KEY is not configured."
            )
        if not (1 <= days_back <= 365 and 1 <= window_days <= 7):
            raise ValueError("HISTORY_WINDOW_OUT_OF_BOUNDS")
        now = self.clock.now()
        start = now - timedelta(days=days_back)
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        while start < now:
            end = min(start + timedelta(days=window_days), now)
            endpoint = (
                f"/matches?dateFrom={start:%Y-%m-%d}"
                f"&dateTo={end:%Y-%m-%d}&status=FINISHED"
            )
            data = self._make_request(endpoint)
            for raw in data.get("matches", []):
                clean = OddsExclusionPolicy.sanitize_payload(raw)
                if clean.get("status") != "FINISHED":
                    continue
                comp_info = clean.get("competition") or {}
                comp_id = comp_info.get("id") or comp_info.get("code")
                if not ProviderCompetitionMapper.is_supported(comp_id):
                    continue
                home = clean.get("homeTeam") or {}
                away = clean.get("awayTeam") or {}
                home_id = self.team_mapper.map_team(
                    str(home.get("id", "")), home.get("name", ""), allow_unresolved=True
                )
                away_id = self.team_mapper.map_team(
                    str(away.get("id", "")), away.get("name", ""), allow_unresolved=True
                )
                season = clean.get("season") or {}
                start_date = season.get("startDate")
                end_date = season.get("endDate")
                kickoff_str = clean.get("utcDate")
                scores = (clean.get("score") or {}).get("fullTime") or {}
                h_score, a_score = scores.get("home"), scores.get("away")
                provider_id = str(clean.get("id", ""))
                if (
                    not provider_id or provider_id in seen or not kickoff_str
                    or not start_date or not end_date
                    or not isinstance(h_score, int) or not isinstance(a_score, int)
                    or h_score < 0 or a_score < 0
                ):
                    continue
                kickoff = datetime.fromisoformat(
                    kickoff_str.replace("Z", "+00:00")
                ).astimezone(UTC)
                if kickoff >= now:
                    continue
                seen.add(provider_id)
                output.append({
                    "provider_name": self.provider_name,
                    "provider_fixture_id": provider_id,
                    "fixture_id": uuid5(NAMESPACE_URL, f"{self.provider_name}:{provider_id}"),
                    "competition_code": ProviderCompetitionMapper.map_competition(comp_id),
                    "season_year": start_date[:4],
                    "season_start_date": start_date,
                    "season_end_date": end_date,
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "kickoff_utc": kickoff,
                    "home_score": h_score,
                    "away_score": a_score,
                    "status": "FINISHED",
                    "payload_fingerprint": compute_payload_fingerprint(clean),
                    "retrieved_at_utc": now,
                    "is_mapped": home_id is not None and away_id is not None,
                })
            start = end
        return output

    def get_fixture_results(
        self,
        provider_fixture_ids: list[str],
    ) -> list[LiveResultDTO]:
        """Fetch finalized or updated match scores."""
        results: list[LiveResultDTO] = []
        retrieved_at = self.clock.now()

        for fid in provider_fixture_ids:
            path = f"/matches/{fid}"
            data = self._make_request(path)
            clean_data = OddsExclusionPolicy.sanitize_payload(data)
            fp = compute_payload_fingerprint(clean_data)

            status_str = clean_data.get("status", "SCHEDULED").upper()
            try:
                status = LiveFixtureStatus(status_str)
            except ValueError:
                status = LiveFixtureStatus.SCHEDULED

            score_data = clean_data.get("score", {}).get("fullTime", {})
            home_score = score_data.get("home")
            away_score = score_data.get("away")
            is_final = status == LiveFixtureStatus.FINISHED and home_score is not None and away_score is not None

            results.append(
                LiveResultDTO(
                    provider_fixture_id=str(fid),
                    provider_name=self.provider_name,
                    status=status,
                    home_score=home_score,
                    away_score=away_score,
                    is_final=is_final,
                    result_version=1,
                    retrieved_at_utc=retrieved_at,
                    payload_fingerprint=fp,
                    raw_payload=clean_data,
                )
            )

        return results

    def get_rate_limit_state(self) -> ProviderRateLimitState:
        return self._rate_limit_state

    def _make_request(self, endpoint: str) -> dict[str, Any]:
        """Internal dispatch with error classification, headers, and rate limiting."""
        url = f"{self._base_url}{endpoint}"

        if self.transport is not None:
            # Test double transport execution
            status_code, headers, body_str = self.transport(endpoint)
            self._parse_headers(headers)
            return self._handle_response_body(status_code, body_str)

        req = urllib.request.Request(
            url,
            headers={
                "X-Auth-Token": self._api_key or "",
                "User-Agent": "FootballIntelligence/1.0",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                headers = dict(resp.headers)
                self._parse_headers(headers)
                body = resp.read().decode("utf-8")
                return self._handle_response_body(resp.status, body)
        except urllib.error.HTTPError as err:
            headers = dict(err.headers)
            self._parse_headers(headers)
            body = err.read().decode("utf-8", errors="replace")
            self._handle_http_error(err.code, body)
        except urllib.error.URLError as err:
            raise TransientProviderError(
                "PROVIDER_CONNECTION_ERROR",
                f"Network failure connecting to provider endpoint '{endpoint}'.",
                {"error": str(err.reason)},
            ) from err

    def _handle_http_error(self, code: int, body: str) -> None:
        if code in (401, 403):
            raise PermanentProviderError(
                "PROVIDER_AUTHENTICATION_FAILURE",
                f"Provider authentication failed with HTTP {code}. Credentials rejected.",
                {"status_code": code},
            )
        elif code == 429:
            retry_after = self._rate_limit_state.reset_seconds or 60
            raise TransientProviderError(
                "PROVIDER_RATE_LIMITED",
                f"Provider rate limit exceeded (HTTP 429). Retry after {retry_after} seconds.",
                {"status_code": code, "retry_after": retry_after},
            )
        elif code >= 500:
            raise TransientProviderError(
                "PROVIDER_SERVER_ERROR",
                f"Provider server returned temporary HTTP {code}.",
                {"status_code": code},
            )
        else:
            raise OperationalException(
                ErrorCategory.DATA,
                Retryability.PERMANENT,
                "PROVIDER_UNEXPECTED_STATUS",
                f"Provider returned unexpected HTTP {code}: {body[:200]}",
                {"status_code": code},
            )

    def _handle_response_body(self, status_code: int, body_str: str) -> dict[str, Any]:
        if status_code != 200:
            self._handle_http_error(status_code, body_str)

        try:
            data = json.loads(body_str)
        except Exception as exc:
            raise OperationalException(
                ErrorCategory.DATA,
                Retryability.PERMANENT,
                "MALFORMED_PROVIDER_PAYLOAD",
                "Provider returned invalid non-JSON payload.",
                {"error": str(exc)},
            ) from exc

        if not isinstance(data, dict):
            raise OperationalException(
                ErrorCategory.DATA,
                Retryability.PERMANENT,
                "MALFORMED_PROVIDER_PAYLOAD",
                f"Expected JSON object from provider, got {type(data).__name__}.",
            )

        return data

    def _parse_headers(self, headers: dict[str, Any]) -> None:
        """Extract rate limit quota and detect clock skew."""
        remaining_str = headers.get("X-Requests-Available-Minute") or headers.get("x-requests-available-minute")
        remaining = int(remaining_str) if remaining_str and remaining_str.isdigit() else None

        reset_str = headers.get("Retry-After") or headers.get("retry-after")
        reset_seconds = int(reset_str) if reset_str and reset_str.isdigit() else None

        self._rate_limit_state = ProviderRateLimitState(
            remaining=remaining,
            reset_seconds=reset_seconds,
            last_checked_at=self.clock.now(),
        )

        date_hdr = headers.get("Date") or headers.get("date")
        if date_hdr:
            try:
                # RFC 2822 date parsing
                from email.utils import parsedate_to_datetime
                server_dt = parsedate_to_datetime(date_hdr).astimezone(UTC)
                self._clock_skew_seconds = (self.clock.now() - server_dt).total_seconds()
            except Exception:
                pass
