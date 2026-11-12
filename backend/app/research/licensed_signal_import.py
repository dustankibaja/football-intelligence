"""Research-only import contract for rights-reviewed, timestamped football signals.

This module never downloads data, writes the canonical feature store, trains a
model or promotes a champion. A grant/reference and a raw payload digest are
operator-supplied evidence, not automatic proof of provider authorization.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identity = Annotated[str, Field(min_length=1, max_length=120)]
NonNegativeXG = Annotated[float, Field(ge=0, le=20, allow_inf_nan=False)]


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderGrant(StrictRecord):
    provider_key: Identity
    authorization_reference: Annotated[str, Field(min_length=1, max_length=500)]
    permits_internal_research: bool
    permits_model_training: bool
    permits_commercial_publication: bool = False
    expires_at: AwareDatetime | None = None


class SourceReceipt(StrictRecord):
    provider_key: Identity
    source_record_id: Identity
    source_payload_sha256: Digest
    published_at: AwareDatetime
    captured_at: AwareDatetime


class TargetFixture(StrictRecord):
    fixture_id: UUID
    competition_id: UUID
    home_team_id: UUID
    away_team_id: UUID
    kickoff_utc: AwareDatetime
    cutoff_utc: AwareDatetime


class PriorMatchStats(StrictRecord):
    fixture_id: UUID
    team_id: UUID
    completed_at: AwareDatetime
    xg_for: NonNegativeXG
    xg_against: NonNegativeXG
    receipt: SourceReceipt


class TeamAvailability(StrictRecord):
    fixture_id: UUID
    team_id: UUID
    # None = unknown. An empty unavailable list = explicitly known zero.
    confirmed_starters: list[UUID] | None = None
    unavailable_players: list[UUID] | None = None
    receipt: SourceReceipt


class SignalBundle(StrictRecord):
    schema_version: str
    target: TargetFixture
    grants: list[ProviderGrant]
    prior_matches: list[PriorMatchStats]
    availability: list[TeamAvailability]


def _digest(obj) -> str:
    return sha256(json.dumps(obj, sort_keys=True, allow_nan=False,
                            separators=(",", ":")).encode()).hexdigest()


def materialize_signals(bundle: SignalBundle) -> dict:
    """Validate pre-match source timing, identity, coverage and missingness.

    All source data must have been captured at or before the prediction cutoff.
    An archive loaded now is eligible for historical training ONLY if it
    preserves authentic original as-of receipt timestamps. An operator must
    independently authenticate the original payload, rights and canonical IDs.
    The resulting feature vector belongs to a new research schema, not C1.
    """
    if bundle.schema_version != "licensed_football_signals_1.0.0":
        raise ValueError("UNSUPPORTED_LICENSED_SIGNAL_SCHEMA")
    target = bundle.target
    cutoff = target.cutoff_utc.astimezone(UTC)
    if cutoff >= target.kickoff_utc.astimezone(UTC):
        raise ValueError("SIGNAL_CUTOFF_NOT_PREMATCH")
    if target.home_team_id == target.away_team_id:
        raise ValueError("SIGNAL_TARGET_TEAMS_NOT_DISTINCT")
    grants = {g.provider_key: g for g in bundle.grants}
    if len(grants) != len(bundle.grants):
        raise ValueError("DUPLICATE_SOURCE_GRANT")
    if not grants:
        raise ValueError("SOURCE_GRANT_REQUIRED")
    for grant in grants.values():
        if not grant.authorization_reference.strip() or not (
            grant.permits_internal_research and grant.permits_model_training
        ):
            raise ValueError("SOURCE_RIGHTS_DO_NOT_COVER_RESEARCH_AND_TRAINING")
        if grant.expires_at and grant.expires_at < cutoff:
            raise ValueError("SOURCE_GRANT_EXPIRED")

    teams = {target.home_team_id, target.away_team_id}
    used_receipts = []

    def check_receipt(receipt: SourceReceipt) -> None:
        if receipt.provider_key not in grants:
            raise ValueError("SOURCE_GRANT_MISSING_FOR_OBSERVATION")
        if receipt.published_at > receipt.captured_at or receipt.captured_at > cutoff:
            raise ValueError("SOURCE_SIGNAL_NOT_KNOWN_BEFORE_CUTOFF")
        used_receipts.append(receipt.model_dump(mode="json"))

    stats = {team: [] for team in teams}
    seen = set()
    for match in bundle.prior_matches:
        if match.team_id not in teams or match.fixture_id == target.fixture_id:
            raise ValueError("SOURCE_PRIOR_MATCH_IDENTITY_MISMATCH")
        key = (match.team_id, match.fixture_id)
        if key in seen:
            raise ValueError("DUPLICATE_PRIOR_MATCH_TEAM_STATS")
        seen.add(key)
        if match.completed_at > match.receipt.published_at or match.completed_at >= cutoff:
            raise ValueError("PRIOR_STATS_NOT_COMPLETED_BEFORE_CUTOFF")
        check_receipt(match.receipt)
        stats[match.team_id].append(match)

    available = {}
    for item in bundle.availability:
        if item.fixture_id != target.fixture_id or item.team_id not in teams:
            raise ValueError("SOURCE_AVAILABILITY_IDENTITY_MISMATCH")
        if item.team_id in available:
            raise ValueError("DUPLICATE_TEAM_AVAILABILITY_SNAPSHOT")
        check_receipt(item.receipt)
        starters, missing = item.confirmed_starters, item.unavailable_players
        if starters is not None and (len(starters) != 11 or len(set(starters)) != 11):
            raise ValueError("CONFIRMED_LINEUP_REQUIRES_11_DISTINCT_PLAYERS")
        if missing is not None and len(set(missing)) != len(missing):
            raise ValueError("DUPLICATE_UNAVAILABLE_PLAYER")
        if starters is not None and missing is not None and set(starters).intersection(missing):
            raise ValueError("PLAYER_BOTH_STARTING_AND_UNAVAILABLE")
        available[item.team_id] = item

    features = {}
    missingness = {}
    team_coverage = {}
    for side, team in (("home", target.home_team_id), ("away", target.away_team_id)):
        recent = sorted(stats[team], key=lambda row: (row.completed_at, str(row.fixture_id)),
                        reverse=True)[:5]
        state = available.get(team)
        count = len(recent)
        # Do not silently make a 1-4 match average look like a five-match input.
        values = {
            "xg_for_last5": sum(x.xg_for for x in recent) / 5 if count == 5 else None,
            "xg_against_last5": sum(x.xg_against for x in recent) / 5 if count == 5 else None,
            "prior_match_count": count,
            "confirmed_lineup": 1 if state and state.confirmed_starters is not None else None,
            "unavailable_player_count": (
                len(state.unavailable_players)
                if state and state.unavailable_players is not None else None
            ),
        }
        for key, value in values.items():
            field = f"{side}_{key}"
            features[field] = value
            if value is None:
                missingness[field] = (
                    "INSUFFICIENT_FIVE_PRIOR_MATCHES"
                    if key.startswith("xg_") else "NO_ASOF_VERIFIED_TEAM_SNAPSHOT"
                )
        team_coverage[side] = {
            "prior_matches_used": count,
            "lineup_known": values["confirmed_lineup"] is not None,
            "availability_known": values["unavailable_player_count"] is not None,
        }
    normalized = bundle.model_dump(mode="json")
    result = {
        "schema_version": "licensed_signal_research_features_1.0.0",
        "status": "STRUCTURE_VALIDATED_ORIGIN_AND_RIGHTS_REQUIRE_INDEPENDENT_REVIEW",
        "fixture_id": str(target.fixture_id),
        "cutoff_utc": cutoff.isoformat(),
        "kickoff_utc": target.kickoff_utc.astimezone(UTC).isoformat(),
        "features": features,
        "missingness": missingness,
        "coverage": team_coverage,
        "complete_feature_coverage": not missingness,
        "publication_eligible": False,
        "source_bundle_sha256": _digest(normalized),
        "source_receipts": sorted(used_receipts, key=lambda x: (
            x["provider_key"], x["source_record_id"], x["source_payload_sha256"]
        )),
        "limitations": [
            "No provider access, license verification, model fitting or production publication performed.",
            "Canonical fixture/team/player IDs and source as-of timestamps must be independently authenticated.",
            "A source grant is a documented operator assertion; do not infer rights from public accessibility.",
            "These inputs cannot be appended to the frozen 251-feature C1 artifact; train a separate challenger.",
            "Complete feature coverage is not evidence of prediction accuracy.",
        ],
    }
    result["feature_report_sha256"] = _digest(result)
    return result


def main():
    parser = argparse.ArgumentParser(description="Validate locally supplied licensed football signal snapshots.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise ValueError("INPUT_SOURCE_BUNDLE_MUST_REMAIN_IMMUTABLE")
    if args.input.stat().st_size > 10_000_000:
        raise ValueError("SIGNAL_BUNDLE_TOO_LARGE")
    bundle = SignalBundle.model_validate_json(args.input.read_bytes())
    result = materialize_signals(bundle)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"status": result["status"], "coverage": result["coverage"],
                      "missingness": result["missingness"], "sha256": result["feature_report_sha256"]},
                     indent=2))


if __name__ == "__main__":
    main()
