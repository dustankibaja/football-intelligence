"""External data access policy: offline by default, explicit reviewed rights required.

An environment reference is an operator assertion, not proof of permission.
Do not configure it until documented source permission covers the specific
use, automation frequency, training, storage and redistribution.
"""
import os

APPROVAL_ENV = "FOOTBALL_DATA_NETWORK_APPROVAL_REFERENCE"


def require_football_data_network_permission() -> None:
    reference = os.environ.get(APPROVAL_ENV, "").strip()
    if not reference or len(reference) > 500:
        raise RuntimeError(
            "FOOTBALL_DATA_NETWORK_ACCESS_DISABLED: supply an approved local "
            "snapshot or obtain documented provider authorization before "
            "enabling automated network downloads."
        )
