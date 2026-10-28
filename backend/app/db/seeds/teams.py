"""
Canonical Team Registry & Deterministic Provisioning System
Provisions certified promoted / newly registered teams into the relational database
using repository-controlled canonical artifacts with zero historical matches (true cold-start).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models.core import Team

logger = logging.getLogger(__name__)

CANONICAL_TEAMS_SEED_PATH = Path(__file__).resolve().parent / "canonical_teams_registry.json"


def get_canonical_teams_seed_path() -> Path:
    if CANONICAL_TEAMS_SEED_PATH.exists():
        return CANONICAL_TEAMS_SEED_PATH
    # Fallback to repo data/seeds directory
    repo_seed = Path(__file__).resolve().parents[4] / "data" / "seeds" / "canonical_teams_registry.json"
    if repo_seed.exists():
        return repo_seed
    return CANONICAL_TEAMS_SEED_PATH


def load_canonical_teams_registry() -> list[dict[str, Any]]:
    path = get_canonical_teams_seed_path()
    if not path.exists():
        raise FileNotFoundError(f"Canonical teams registry seed missing at {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def get_canonical_teams_fingerprint() -> str:
    data = load_canonical_teams_registry()
    canonical_bytes = json.dumps(data, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def provision_canonical_teams(session: Session) -> list[Team]:
    """
    Idempotently provisions the certified canonical teams into the database.
    Guarantees deterministic UUIDs across fresh SQLite and PostgreSQL environments.
    Guarantees zero historical matches are inserted (true cold start).
    """
    registry = load_canonical_teams_registry()
    provisioned_teams: list[Team] = []

    for entry in registry:
        team_id = UUID(entry["id"])
        name = entry["canonical_name"]
        short_name = entry.get("short_name", name)
        country = entry.get("country", "Unknown")
        is_active = entry.get("is_active", True)

        # Check by ID or canonical name
        existing = session.query(Team).filter(
            (Team.id == team_id) | (Team.canonical_name == name)
        ).first()

        if existing is not None:
            # Invariant check: deterministic ID must match
            if existing.id != team_id:
                raise ValueError(
                    f"Deterministic team ID mismatch for '{name}': "
                    f"existing={existing.id}, expected={team_id}"
                )
            provisioned_teams.append(existing)
        else:
            team_obj = Team(
                id=team_id,
                canonical_name=name,
                short_name=short_name,
                country=country,
                is_active=is_active,
            )
            session.add(team_obj)
            provisioned_teams.append(team_obj)

    session.commit()
    return provisioned_teams


if __name__ == "__main__":
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        teams = provision_canonical_teams(db)
        print(f"Successfully provisioned/verified {len(teams)} canonical teams.")
        for t in teams:
            print(f"  - {t.canonical_name:<15}: {t.id} ({t.country})")
