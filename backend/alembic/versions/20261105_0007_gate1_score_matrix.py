"""Persist the complete frozen exact-score distribution for scientific scoring.

Revision ID: 20261105_0007
Revises: 20261023_0006
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_0007"
down_revision: str | None = "20261023_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing predictions are intentionally left NULL: no historical matrix
    # may be fabricated and attached retroactively to a prior prediction.
    op.add_column("shadow_predictions", sa.Column("score_matrix", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("shadow_predictions", "score_matrix")
