"""
Sprint 7.2 Competition, Team & Odds Isolation Policies
Enforces deterministic mapping for certified competitions (E0, SP1, I1, D1),
rejects unsupported leagues and fuzzy auto-matching, and guarantees strict odds exclusion.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from app.automation.errors import ErrorCategory, OperationalException, Retryability

SUPPORTED_COMPETITIONS: dict[str, str] = {
    "E0": "Premier League",
    "SP1": "La Liga",
    "I1": "Serie A",
    "D1": "Bundesliga",
}

FORBIDDEN_ODDS_KEYS: set[str] = {
    "odds",
    "bookmakers",
    "bets",
    "betting",
    "markets",
    "prices",
    "lines",
    "spreads",
    "handicap",
    "b365",
    "bw",
    "iw",
    "ps",
    "wh",
    "vc",
    "market_max",
    "market_avg",
}


class UnsupportedCompetitionError(OperationalException):
    def __init__(self, raw_competition: str | int) -> None:
        super().__init__(
            ErrorCategory.DATA,
            Retryability.PERMANENT,
            "UNSUPPORTED_COMPETITION",
            f"Competition '{raw_competition}' is not within the certified domain (E0, SP1, I1, D1).",
            {"raw_competition": str(raw_competition)},
        )


class UnresolvedTeamMappingError(OperationalException):
    def __init__(self, provider_team_id: str, provider_team_name: str) -> None:
        super().__init__(
            ErrorCategory.DATA,
            Retryability.PERMANENT,
            "UNRESOLVED_TEAM_MAPPING",
            f"Team '{provider_team_name}' (ID: {provider_team_id}) cannot be mapped deterministically to a canonical entity.",
            {"provider_team_id": provider_team_id, "provider_team_name": provider_team_name},
        )


class ProviderCompetitionMapper:
    """Deterministic mapper from external provider league identifiers to canonical competition codes."""

    # Provider ID / Code to canonical code
    _EXPLICIT_MAPPING: dict[str, str] = {
        # Football-Data.org codes / IDs
        "2021": "E0",
        "PL": "E0",
        "PREMIER_LEAGUE": "E0",
        "PREMIER LEAGUE": "E0",
        "2014": "SP1",
        "PD": "SP1",
        "LA_LIGA": "SP1",
        "PRIMERA_DIVISION": "SP1",
        "LA LIGA": "SP1",
        "2019": "I1",
        "SA": "I1",
        "SERIE_A": "I1",
        "SERIE A": "I1",
        "2002": "D1",
        "BL1": "D1",
        "BUNDESLIGA": "D1",
        # Football-Data.co.uk codes
        "E0": "E0",
        "SP1": "SP1",
        "I1": "I1",
        "D1": "D1",
    }

    @classmethod
    def map_competition(cls, provider_competition_id_or_code: str | int) -> str:
        key = str(provider_competition_id_or_code).strip().upper()
        if key in cls._EXPLICIT_MAPPING:
            canonical = cls._EXPLICIT_MAPPING[key]
            if canonical in SUPPORTED_COMPETITIONS:
                return canonical
        raise UnsupportedCompetitionError(provider_competition_id_or_code)

    @classmethod
    def is_supported(cls, provider_competition_id_or_code: str | int) -> bool:
        key = str(provider_competition_id_or_code).strip().upper()
        return key in cls._EXPLICIT_MAPPING and cls._EXPLICIT_MAPPING[key] in SUPPORTED_COMPETITIONS


class ProviderTeamMapper:
    """
    Deterministic mapper for provider teams to canonical team entities.
    Strictly forbids fuzzy matching or silent auto-acceptance of unknown teams.
    """

    _EXPLICIT_PRE_REGISTERED: dict[str, str] = {
        # Premier League
        "61": "7f8398f8-0dac-419b-9af3-7c28b1bcba06",
        "CHELSEAFC": "7f8398f8-0dac-419b-9af3-7c28b1bcba06",
        "66": "0071a470-a49a-4f64-91e3-535f075c6f13",
        "MANCHESTERUNITEDFC": "0071a470-a49a-4f64-91e3-535f075c6f13",
        "64": "b06d38ec-ef24-4c54-aa29-d9dae461abc6",
        "LIVERPOOLFC": "b06d38ec-ef24-4c54-aa29-d9dae461abc6",
        "57": "915c37ca-4a80-4fa9-803c-adb35be3e33d",
        "ARSENALFC": "915c37ca-4a80-4fa9-803c-adb35be3e33d",
        "65": "8e4da039-3e70-4fd9-a489-c5f6e08110c2",
        "MANCHESTERCITYFC": "8e4da039-3e70-4fd9-a489-c5f6e08110c2",
        "73": "4bbe0db5-5a7a-4edb-b208-847970d6f785",
        "TOTTENHAMHOTSPURFC": "4bbe0db5-5a7a-4edb-b208-847970d6f785",
        "58": "c84dd3e1-50ab-4b37-8be9-6c8b26fa2e5a",
        "ASTONVILLAFC": "c84dd3e1-50ab-4b37-8be9-6c8b26fa2e5a",
        "63": "45e5e701-45eb-43a4-bcb5-dcc61aeb105e",
        "FULHAMFC": "45e5e701-45eb-43a4-bcb5-dcc61aeb105e",
        "62": "3cb41444-044d-4328-87ac-248a0fe1741c",
        "EVERTONFC": "3cb41444-044d-4328-87ac-248a0fe1741c",
        "67": "f75807b1-81ca-4ff3-a4ec-61fc5dd2170a",
        "NEWCASTLEUNITEDFC": "f75807b1-81ca-4ff3-a4ec-61fc5dd2170a",
        "402": "586cbf92-e1ea-4450-a37c-48a1eebb599b",
        "BRENTFORDFC": "586cbf92-e1ea-4450-a37c-48a1eebb599b",
        "351": "0bbc2be3-478b-4dd7-9a8f-cda0d1e3c2da",
        "NOTTINGHAMFORESTFC": "0bbc2be3-478b-4dd7-9a8f-cda0d1e3c2da",
        "354": "298bd0cf-b2fb-4917-b495-571462f12eb2",
        "CRYSTALPALACEFC": "298bd0cf-b2fb-4917-b495-571462f12eb2",
        # Serie A
        "98": "e3d92f3a-d75b-4e69-8960-a20f363cd5b6",
        "ACMILAN": "e3d92f3a-d75b-4e69-8960-a20f363cd5b6",
        "103": "4341da35-eb86-4eef-813f-bd0e2cd7c27c",
        "BOLOGNAFC1909": "4341da35-eb86-4eef-813f-bd0e2cd7c27c",
        "108": "5350a533-a886-45a0-b01c-c7a2e0c43250",
        "FCINTERNAZIONALEMILANO": "5350a533-a886-45a0-b01c-c7a2e0c43250",
        "107": "b5fdf9a1-33d7-4773-9579-6cd53658c042",
        "GENOACFC": "b5fdf9a1-33d7-4773-9579-6cd53658c042",
        "109": "0d709406-4d91-47e6-b7af-1222c35a7210",
        "JUVENTUSFC": "0d709406-4d91-47e6-b7af-1222c35a7210",
        "99": "46bd532f-e8a0-4174-bdc9-f33b4d617ab1",
        "ACFFIORENTINA": "46bd532f-e8a0-4174-bdc9-f33b4d617ab1",
        "102": "85a85789-dc68-4aab-af17-55eab7605682",
        "ATALANTABC": "85a85789-dc68-4aab-af17-55eab7605682",
        "100": "5901cfc2-f6e7-4351-bedc-18a816f72785",
        "ASROMA": "5901cfc2-f6e7-4351-bedc-18a816f72785",
        "113": "194c86e0-c50c-44eb-b563-15502b060bc0",
        "SSCNAPOLI": "194c86e0-c50c-44eb-b563-15502b060bc0",
        "115": "c2c9fbb4-ec86-4030-b449-c545386c8639",
        "UDINESECALCIO": "c2c9fbb4-ec86-4030-b449-c545386c8639",
        # Bundesliga
        "5": "ac707ca5-3d2b-4555-89ec-a8759ba299d1",
        "FCBAYERNMUNCHEN": "ac707ca5-3d2b-4555-89ec-a8759ba299d1",
        "4": "5e1a1672-0edc-4d51-8b3f-463be6c2d3d8",
        "BORUSSIADORTMUND": "5e1a1672-0edc-4d51-8b3f-463be6c2d3d8",
        "3": "9c3ec603-8602-41ac-bbc2-f348b6e4fb1a",
        "BAYER04LEVERKUSEN": "9c3ec603-8602-41ac-bbc2-f348b6e4fb1a",
        "10": "78d99ee8-c5e7-43db-b8bc-64be77f69a73",
        "VFBSTUTTGART": "78d99ee8-c5e7-43db-b8bc-64be77f69a73",
        "6": "7b80d631-7fbe-4831-8af1-d31fde074412",
        "FCSCHALKE04": "7b80d631-7fbe-4831-8af1-d31fde074412",
        "721": "122c5680-cdd2-4c32-b36d-a3897768680a",
        "RBLEIPZIG": "122c5680-cdd2-4c32-b36d-a3897768680a",
        # La Liga
        "81": "261c78e3-2763-4516-b138-8f645fc90f17",
        "FCBARCELONA": "261c78e3-2763-4516-b138-8f645fc90f17",
        "86": "128247a1-b33b-4bfe-aaa5-08234624d0a5",
        "REALMADRIDCF": "128247a1-b33b-4bfe-aaa5-08234624d0a5",
        "78": "6a5dd07a-5e20-4b2d-a5ab-5b86128a69f2",
        "CLUBATLETICODEMADRID": "6a5dd07a-5e20-4b2d-a5ab-5b86128a69f2",
        "94": "af1f4345-6561-48da-9177-1bcc8ea61c99",
        "VILLARREALCF": "af1f4345-6561-48da-9177-1bcc8ea61c99",
        "80": "9438471d-c288-431d-a638-ddd5b39137e1",
        "RCDESPANYOLDEBARCELONA": "9438471d-c288-431d-a638-ddd5b39137e1",
    }

    def __init__(self, explicit_mappings: dict[str, UUID] | None = None) -> None:
        # Load certified pre-registered mappings and overlay explicit mappings
        self._mappings: dict[str, UUID] = {
            k: UUID(v) for k, v in self._EXPLICIT_PRE_REGISTERED.items()
        }
        if explicit_mappings:
            for k, v in explicit_mappings.items():
                self._mappings[str(k).strip()] = v

    def register_mapping(self, provider_team_id: str, canonical_id: UUID) -> None:
        self._mappings[str(provider_team_id).strip()] = canonical_id

    def map_team(
        self,
        provider_team_id: str,
        provider_team_name: str,
        allow_unresolved: bool = False,
    ) -> UUID | None:
        """
        Deterministically resolve team identity.
        If unknown and allow_unresolved is False, raises UnresolvedTeamMappingError.
        """
        key = str(provider_team_id).strip()
        if key in self._mappings:
            return self._mappings[key]

        # Name fallback only if exact pre-registered alias exists (never fuzzy matching)
        clean_name = self._normalize_name(provider_team_name)
        if clean_name in self._mappings:
            return self._mappings[clean_name]

        if allow_unresolved:
            return None

        raise UnresolvedTeamMappingError(provider_team_id, provider_team_name)

    @staticmethod
    def _normalize_name(name: str) -> str:
        # Standard uppercase alphanumeric only
        return re.sub(r"[^A-Z0-9]", "", name.upper())


class OddsExclusionPolicy:
    """Audits raw payloads and ensures bookmaker odds are stripped and excluded from feature pipelines."""

    @classmethod
    def sanitize_payload(cls, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Recursively removes any keys related to odds, betting markets, or bookmakers."""
        sanitized: dict[str, Any] = {}
        for k, v in raw_data.items():
            k_lower = k.lower()
            if any(forbidden in k_lower for forbidden in FORBIDDEN_ODDS_KEYS):
                continue
            if isinstance(v, dict):
                sanitized[k] = cls.sanitize_payload(v)
            elif isinstance(v, list):
                sanitized[k] = [cls.sanitize_payload(item) if isinstance(item, dict) else item for item in v]
            else:
                sanitized[k] = v
        return sanitized

    @classmethod
    def assert_no_odds(cls, payload: dict[str, Any]) -> None:
        for k in payload:
            k_lower = k.lower()
            if any(forbidden in k_lower for forbidden in FORBIDDEN_ODDS_KEYS):
                raise OperationalException(
                    ErrorCategory.SCIENTIFIC_INTEGRITY,
                    Retryability.PERMANENT,
                    "ODDS_CONTAMINATION_DETECTED",
                    f"Forbidden odds key '{k}' detected in sanitized provider observation.",
                )
