"""
Distributed Lease Management with Monotonic Fencing Token (Sections 8-11, 46)
Provides distributed concurrency control, heartbeat renewal, expiry takeover,
and monotonic fencing token validation to guarantee split-brain protection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.automation.clock import Clock, SystemClock
from app.db.models.automation import SchedulerLease


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is offset-aware in UTC for consistent cross-database comparisons."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass
class LeaseRecord:
    """In-memory and transferred representation of a distributed scheduler lease."""
    id: UUID
    lease_name: str
    lease_owner: str
    fencing_token: int
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime
    is_active: bool
    lease_metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, current_time: datetime) -> bool:
        return _ensure_utc(current_time) >= _ensure_utc(self.expires_at)


class LeaseManager:
    """
    Manages distributed lease acquisition, renewal, release, and fencing token validation.
    Guarantees that each lease acquisition produces a strictly monotonically increasing fencing token.
    Stale workers holding obsolete fencing tokens are permanently rejected from protected commits.
    """

    def __init__(self, clock: Clock | None = None, session_factory: Any = None) -> None:
        self.clock = clock or SystemClock()
        self.session_factory = session_factory
        # Memory storage for unit testing / standalone execution
        self._leases: dict[str, LeaseRecord] = {}

    def acquire(
        self,
        lease_name: str,
        lease_owner: str,
        ttl_seconds: float = 60.0,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, LeaseRecord]:
        """
        Attempt to acquire or re-acquire a lease.
        Returns (True, LeaseRecord) on success, or (False, current_lease) on contention.
        """
        now = self.clock.now()
        expires_at = now + timedelta(seconds=ttl_seconds)

        # 1. Database-backed acquisition if session_factory is configured
        if self.session_factory is not None:
            with self.session_factory() as session:
                query = session.query(SchedulerLease).filter_by(lease_name=lease_name)
                if session.bind and getattr(session.bind.dialect, "name", "") == "postgresql":
                    query = query.with_for_update()
                lease_obj = query.first()

                if lease_obj is None:
                    # First acquisition: token = 1
                    lease_id = uuid4()
                    lease_obj = SchedulerLease(
                        id=lease_id,
                        lease_name=lease_name,
                        lease_owner=lease_owner,
                        fencing_token=1,
                        acquired_at=now,
                        heartbeat_at=now,
                        expires_at=expires_at,
                        is_active=True,
                        lease_metadata=metadata or {},
                    )
                    session.add(lease_obj)
                    session.commit()
                    record = LeaseRecord(
                        id=lease_obj.id,
                        lease_name=lease_name,
                        lease_owner=lease_owner,
                        fencing_token=1,
                        acquired_at=now,
                        heartbeat_at=now,
                        expires_at=expires_at,
                        is_active=True,
                        lease_metadata=metadata or {},
                    )
                    self._leases[lease_name] = record
                    return True, record

                # Existing DB lease: check if active and unexpired
                if lease_obj.is_active and _ensure_utc(now) < _ensure_utc(lease_obj.expires_at):
                    if lease_obj.lease_owner == lease_owner:
                        # Re-acquire / extend by same owner: increment monotonic generation
                        lease_obj.fencing_token += 1
                        lease_obj.heartbeat_at = now
                        lease_obj.expires_at = expires_at
                        if metadata is not None:
                            lease_obj.lease_metadata = metadata
                        session.commit()
                        record = LeaseRecord(
                            id=lease_obj.id,
                            lease_name=lease_name,
                            lease_owner=lease_owner,
                            fencing_token=lease_obj.fencing_token,
                            acquired_at=now,
                            heartbeat_at=now,
                            expires_at=expires_at,
                            is_active=True,
                            lease_metadata=lease_obj.lease_metadata or {},
                        )
                        self._leases[lease_name] = record
                        return True, record

                    # Contention! Another active worker owns this lease
                    current_record = LeaseRecord(
                        id=lease_obj.id,
                        lease_name=lease_name,
                        lease_owner=lease_obj.lease_owner,
                        fencing_token=lease_obj.fencing_token,
                        acquired_at=lease_obj.acquired_at,
                        heartbeat_at=lease_obj.heartbeat_at,
                        expires_at=lease_obj.expires_at,
                        is_active=lease_obj.is_active,
                        lease_metadata=lease_obj.lease_metadata or {},
                    )
                    self._leases[lease_name] = current_record
                    return False, current_record

                # Lease is inactive or expired: Re-acquisition allowed by any worker!
                # Strictly increment fencing token generation monotonically
                lease_obj.fencing_token += 1
                lease_obj.lease_owner = lease_owner
                lease_obj.is_active = True
                lease_obj.acquired_at = now
                lease_obj.heartbeat_at = now
                lease_obj.expires_at = expires_at
                lease_obj.lease_metadata = metadata or {}
                session.commit()
                record = LeaseRecord(
                    id=lease_obj.id,
                    lease_name=lease_name,
                    lease_owner=lease_owner,
                    fencing_token=lease_obj.fencing_token,
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=expires_at,
                    is_active=True,
                    lease_metadata=lease_obj.lease_metadata or {},
                )
                self._leases[lease_name] = record
                return True, record

        # 2. In-memory atomic store (for isolated tests)
        current = self._leases.get(lease_name)

        if current is None:
            # First acquisition: token = 1
            record = LeaseRecord(
                id=uuid4(),
                lease_name=lease_name,
                lease_owner=lease_owner,
                fencing_token=1,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=expires_at,
                is_active=True,
                lease_metadata=metadata or {},
            )
            self._leases[lease_name] = record
            return True, record

        # Lease exists. Check if active and unexpired
        if current.is_active and not current.is_expired(now):
            # Same owner can re-acquire / refresh
            if current.lease_owner == lease_owner:
                new_token = current.fencing_token + 1
                record = LeaseRecord(
                    id=current.id,
                    lease_name=lease_name,
                    lease_owner=lease_owner,
                    fencing_token=new_token,
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=expires_at,
                    is_active=True,
                    lease_metadata=metadata or current.lease_metadata,
                )
                self._leases[lease_name] = record
                return True, record

            # Active, unexpired, different owner -> Contention!
            return False, current

        # Lease is inactive OR expired: Re-acquisition allowed by any worker!
        new_token = current.fencing_token + 1
        record = LeaseRecord(
            id=current.id,
            lease_name=lease_name,
            lease_owner=lease_owner,
            fencing_token=new_token,
            acquired_at=now,
            heartbeat_at=now,
            expires_at=expires_at,
            is_active=True,
            lease_metadata=metadata or {},
        )
        self._leases[lease_name] = record
        return True, record

    def heartbeat(
        self,
        lease_name: str,
        lease_owner: str,
        fencing_token: int,
        ttl_seconds: float = 60.0,
    ) -> bool:
        """
        Renew an active lease. Requires matching lease_owner and fencing_token.
        Fails if expired or fencing token is stale.
        """
        now = self.clock.now()

        if self.session_factory is not None:
            with self.session_factory() as session:
                lease_obj = session.query(SchedulerLease).filter_by(lease_name=lease_name).first()
                if lease_obj is None or not lease_obj.is_active or _ensure_utc(now) >= _ensure_utc(lease_obj.expires_at):
                    return False
                if lease_obj.lease_owner != lease_owner or lease_obj.fencing_token != fencing_token:
                    return False
                lease_obj.heartbeat_at = now
                lease_obj.expires_at = now + timedelta(seconds=ttl_seconds)
                session.commit()
                if lease_name in self._leases:
                    self._leases[lease_name].heartbeat_at = now
                    self._leases[lease_name].expires_at = lease_obj.expires_at
                return True

        current = self._leases.get(lease_name)
        if current is None:
            return False

        if not current.is_active:
            return False

        if current.is_expired(now):
            return False

        if current.lease_owner != lease_owner or current.fencing_token != fencing_token:
            return False

        current.heartbeat_at = now
        current.expires_at = now + timedelta(seconds=ttl_seconds)
        return True

    def release(
        self,
        lease_name: str,
        lease_owner: str,
        fencing_token: int,
    ) -> bool:
        """
        Release lease ownership gracefully.
        Only succeeds if owner and fencing token match the active lease.
        Preserves row in DB so fencing token generation is monotonic across processes.
        """
        if self.session_factory is not None:
            with self.session_factory() as session:
                lease_obj = session.query(SchedulerLease).filter_by(lease_name=lease_name).first()
                if lease_obj is None:
                    return False
                if lease_obj.lease_owner == lease_owner and lease_obj.fencing_token == fencing_token:
                    lease_obj.is_active = False
                    session.commit()
                    if lease_name in self._leases:
                        self._leases[lease_name].is_active = False
                    return True
                return False

        current = self._leases.get(lease_name)
        if current is None:
            return False

        if current.lease_owner == lease_owner and current.fencing_token == fencing_token:
            current.is_active = False
            return True

        return False

    def validate_token(
        self,
        lease_name: str,
        lease_owner: str,
        fencing_token: int,
    ) -> bool:
        """
        Validate whether the given owner and fencing token are current and authoritative.
        Rejects stale workers whose token is older than the active lease token.
        """
        now = self.clock.now()

        if self.session_factory is not None:
            with self.session_factory() as session:
                lease_obj = session.query(SchedulerLease).filter_by(lease_name=lease_name).first()
                if lease_obj is None or not lease_obj.is_active or _ensure_utc(now) >= _ensure_utc(lease_obj.expires_at):
                    return False
                if lease_obj.lease_owner != lease_owner or lease_obj.fencing_token != fencing_token:
                    return False
                return True

        current = self._leases.get(lease_name)
        if current is None:
            return False

        if not current.is_active:
            return False

        if current.is_expired(now):
            return False

        if current.lease_owner != lease_owner:
            return False

        if current.fencing_token != fencing_token:
            return False

        return True

    def get_lease(self, lease_name: str) -> LeaseRecord | None:
        """Get the current lease record if it exists."""
        if self.session_factory is not None:
            with self.session_factory() as session:
                lease_obj = session.query(SchedulerLease).filter_by(lease_name=lease_name).first()
                if lease_obj is None:
                    return None
                return LeaseRecord(
                    id=lease_obj.id,
                    lease_name=lease_obj.lease_name,
                    lease_owner=lease_obj.lease_owner,
                    fencing_token=lease_obj.fencing_token,
                    acquired_at=lease_obj.acquired_at,
                    heartbeat_at=lease_obj.heartbeat_at,
                    expires_at=lease_obj.expires_at,
                    is_active=lease_obj.is_active,
                    lease_metadata=lease_obj.lease_metadata or {},
                )

        return self._leases.get(lease_name)
