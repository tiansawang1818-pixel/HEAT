"""Add private expiring provider processing checkpoints.

Revision ID: 20261106_0004
Revises: 20261104_0003
"""
from alembic import op
import sqlalchemy as sa

revision = "20261106_0004"
down_revision = "20261104_0003"
branch_labels = None
depends_on = None


def upgrade():
    # The original migration creates current metadata on a brand-new database.
    if sa.inspect(op.get_bind()).has_table("source_processing_checkpoints"):
        return
    op.create_table("source_processing_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("processor_version", sa.String(80), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("chunk_key", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "processor_version", "scope_key", "chunk_key", name="uq_source_processing_chunk"))
    op.create_index("ix_source_processing_checkpoints_source_id", "source_processing_checkpoints", ["source_id"])
    op.create_index("ix_source_processing_checkpoints_expires_at", "source_processing_checkpoints", ["expires_at"])


def downgrade():
    op.drop_table("source_processing_checkpoints")
