"""Allow evidence-pending candidates to exist without fabricated costs."""

from alembic import op
import sqlalchemy as sa


revision = "20261104_0002"
down_revision = "20261031_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("candidate_interventions") as batch:
        batch.alter_column("estimated_cost_thb", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM candidate_interventions WHERE estimated_cost_thb IS NULL")
    with op.batch_alter_table("candidate_interventions") as batch:
        batch.alter_column("estimated_cost_thb", existing_type=sa.Float(), nullable=False)
