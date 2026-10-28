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
        # Bundesliga additions (Sprint 7.2 Reconciliation)
        "1": "ea36e503-93a7-47d5-9e2d-b4e6df29c611",
        "1FCKOLN": "ea36e503-93a7-47d5-9e2d-b4e6df29c611",
        "1FCKLN": "ea36e503-93a7-47d5-9e2d-b4e6df29c611",
        "28": "82b47b62-8a47-4382-a640-8768c0fd1377",
        "1FCUNIONBERLIN": "82b47b62-8a47-4382-a640-8768c0fd1377",
        "15": "fb2826a6-ea87-4d8a-82ad-78cb53ca0518",
        "1FSVMAINZ05": "fb2826a6-ea87-4d8a-82ad-78cb53ca0518",
        "18": "2599eefd-08b7-44ab-ae91-c2fa230fa912",
        "BORUSSIAMONCHENGLADBACH": "2599eefd-08b7-44ab-ae91-c2fa230fa912",
        "BORUSSIAMNCHENGLADBACH": "2599eefd-08b7-44ab-ae91-c2fa230fa912",
        "19": "6887323a-f710-4245-b68b-afa87fc9b697",
        "EINTRACHTFRANKFURT": "6887323a-f710-4245-b68b-afa87fc9b697",
        "16": "f2af6a2d-2d06-4473-b81a-53bee4a37464",
        "FCAUGSBURG": "f2af6a2d-2d06-4473-b81a-53bee4a37464",
        "7": "49b7b4e2-687c-543e-a5df-2c154cbbe189",
        "HAMBURGERSV": "49b7b4e2-687c-543e-a5df-2c154cbbe189",
        "17": "1204fe55-d326-45ee-9cfa-32c0ba4cf8bb",
        "SCFREIBURG": "1204fe55-d326-45ee-9cfa-32c0ba4cf8bb",
        "29": "1cd3abda-a5f5-59cc-95b8-88bced5f15aa",
        "SCPADERBORN07": "1cd3abda-a5f5-59cc-95b8-88bced5f15aa",
        "719": "89f93086-c665-5193-9371-55edee7c7832",
        "SV07ELVERSBERG": "89f93086-c665-5193-9371-55edee7c7832",
        "12": "fb2f88bf-6549-43e3-996a-1e152393550d",
        "SVWERDERBREMEN": "fb2f88bf-6549-43e3-996a-1e152393550d",
        "2": "b8a5c079-70d6-4317-93fa-38113e15b5cf",
        "TSG1899HOFFENHEIM": "b8a5c079-70d6-4317-93fa-38113e15b5cf",
        # Premier League additions (Sprint 7.2 Reconciliation)
        "1044": "5221505b-c44e-4af0-af5b-41a2fc018f8a",
        "AFCBOURNEMOUTH": "5221505b-c44e-4af0-af5b-41a2fc018f8a",
        "397": "8e39336b-b948-4505-bdeb-d31108614aa0",
        "BRIGHTONHOVEALBIONFC": "8e39336b-b948-4505-bdeb-d31108614aa0",
        "1076": "7a1ca804-63c8-5d74-adb9-1863c95b310b",
        "COVENTRYCITYFC": "7a1ca804-63c8-5d74-adb9-1863c95b310b",
        "322": "0f1932a6-d7a7-5a0d-a62b-8ec0c1a38f4f",
        "HULLCITYAFC": "0f1932a6-d7a7-5a0d-a62b-8ec0c1a38f4f",
        "349": "6f3f175b-71a0-550e-8f79-12365da04145",
        "IPSWICHTOWNFC": "6f3f175b-71a0-550e-8f79-12365da04145",
        "341": "f357d1e1-3e78-4e30-a1f5-227defa7d65e",
        "LEEDSUNITEDFC": "f357d1e1-3e78-4e30-a1f5-227defa7d65e",
        "71": "b30f05e6-f12f-5bb1-8050-185f9e7c1f62",
        "SUNDERLANDAFC": "b30f05e6-f12f-5bb1-8050-185f9e7c1f62",
        # Serie A additions (Sprint 7.2 Reconciliation)
        "5911": "c26a7011-91ed-4d92-afab-b2d91a169b54",
        "ACMONZA": "c26a7011-91ed-4d92-afab-b2d91a169b54",
        "104": "70da27f9-dd79-4d7a-811c-821bd546224e",
        "CAGLIARICALCIO": "70da27f9-dd79-4d7a-811c-821bd546224e",
        "7397": "29a9ee53-9012-51af-8556-ccfc8573df78",
        "COMO1907": "29a9ee53-9012-51af-8556-ccfc8573df78",
        "470": "d83ccd1d-cef9-40cd-9859-335c715b9e8b",
        "FROSINONECALCIO": "d83ccd1d-cef9-40cd-9859-335c715b9e8b",
        "112": "fb416f0f-4385-5589-90a9-05e7e4794d0e",
        "PARMACALCIO1913": "fb416f0f-4385-5589-90a9-05e7e4794d0e",
        "110": "7b73e173-b4ad-4efa-911c-49ee8153afc7",
        "SSLAZIO": "7b73e173-b4ad-4efa-911c-49ee8153afc7",
        "586": "760809b0-1500-431e-bd14-03bd7fc1995a",
        "TORINOFC": "760809b0-1500-431e-bd14-03bd7fc1995a",
        "5890": "3d1ce9ec-45a0-45c4-95cf-ae85c2d5dfe4",
        "USLECCE": "3d1ce9ec-45a0-45c4-95cf-ae85c2d5dfe4",
        "471": "94cd2416-c064-4a52-8e76-0a7385d42513",
        "USSASSUOLOCALCIO": "94cd2416-c064-4a52-8e76-0a7385d42513",
        "454": "36b33fe5-e032-4f18-a7c8-cc2db3e74e3d",
        "VENEZIAFC": "36b33fe5-e032-4f18-a7c8-cc2db3e74e3d",
        # La Liga additions (Sprint 7.2 Reconciliation)
        "77": "f6b9bc57-88df-4336-ab6d-5b47d81500ee",
        "ATHLETICCLUB": "f6b9bc57-88df-4336-ab6d-5b47d81500ee",
        "79": "4058ae63-f4af-4df5-a687-5a921767ba05",
        "CAOSASUNA": "4058ae63-f4af-4df5-a687-5a921767ba05",
        "263": "6f5ea33b-32a8-4c5c-a52d-4647d40aa188",
        "DEPORTIVOALAVES": "6f5ea33b-32a8-4c5c-a52d-4647d40aa188",
        "DEPORTIVOALAVS": "6f5ea33b-32a8-4c5c-a52d-4647d40aa188",
        "285": "f1fda44c-3039-4f7d-aa76-887f5ff3aff0",
        "ELCHECF": "f1fda44c-3039-4f7d-aa76-887f5ff3aff0",
        "82": "ab2ed744-7b13-4693-bd33-3a7983444216",
        "GETAFECF": "ab2ed744-7b13-4693-bd33-3a7983444216",
        "88": "f2279528-8cfe-4917-a5cd-02ad2dcfccb8",
        "LEVANTEUD": "f2279528-8cfe-4917-a5cd-02ad2dcfccb8",
        "84": "f3d97136-64a8-5bc2-aede-e2a5acc45224",
        "MALAGACF": "f3d97136-64a8-5bc2-aede-e2a5acc45224",
        "MLAGACF": "f3d97136-64a8-5bc2-aede-e2a5acc45224",
        "558": "70c2d1d7-584c-4c98-a93e-a16b5db1b261",
        "RCCELTADEVIGO": "70c2d1d7-584c-4c98-a93e-a16b5db1b261",
        "560": "e6ea023a-2588-5dbc-a420-4a40e289a72e",
        "RCDEPORTIVOLACORUNA": "e6ea023a-2588-5dbc-a420-4a40e289a72e",
        "RCDEPORTIVOLACORUA": "e6ea023a-2588-5dbc-a420-4a40e289a72e",
        "87": "4c19960d-e62b-4d4f-8b91-042139fcf95b",
        "RAYOVALLECANODEMADRID": "4c19960d-e62b-4d4f-8b91-042139fcf95b",
        "90": "6111e1ab-a0b0-4984-b7bd-2dd650a99852",
        "REALBETISBALOMPIE": "6111e1ab-a0b0-4984-b7bd-2dd650a99852",
        "REALBETISBALOMPI": "6111e1ab-a0b0-4984-b7bd-2dd650a99852",
        "5335": "f26c0af4-4423-58cb-9042-7a4066553bad",
        "REALRACINGCLUBDESANTANDER": "f26c0af4-4423-58cb-9042-7a4066553bad",
        "92": "ea6d2d4c-8738-4a51-9351-ce08df88f26a",
        "REALSOCIEDADDEFUTBOL": "ea6d2d4c-8738-4a51-9351-ce08df88f26a",
        "REALSOCIEDADDEFTBOL": "ea6d2d4c-8738-4a51-9351-ce08df88f26a",
        "559": "1e64992f-cdf1-4e2f-aa5b-7b65f8ac51b8",
        "SEVILLAFC": "1e64992f-cdf1-4e2f-aa5b-7b65f8ac51b8",
        "95": "579d8c4a-df3a-4679-9f72-105f1fc5237f",
        "VALENCIACF": "579d8c4a-df3a-4679-9f72-105f1fc5237f",
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
