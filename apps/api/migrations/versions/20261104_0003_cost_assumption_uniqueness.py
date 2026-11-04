"""Prevent duplicate cost-assumption versions for one intervention type."""

from alembic import op


revision = "20261104_0003"
down_revision = "20261104_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cost_assumptions_type_version "
        "ON cost_assumptions (intervention_type, version)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_cost_assumptions_type_version")
