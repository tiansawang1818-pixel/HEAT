"""Initial HEAT//SHIFT schema.

The application metadata is the single schema contract; using it here keeps the
SQLite test schema and PostGIS deployment schema aligned. Later revisions must
use explicit operations for changes.
"""
from alembic import op
from sqlalchemy import text

from app.db.base import Base
import app.models.entities  # noqa: F401


revision = "20261031_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name == "postgresql":
        bind.execute(text("CREATE INDEX IF NOT EXISTS ix_geographic_areas_geometry_gist ON geographic_areas USING GIST (geometry)"))
        bind.execute(text("CREATE INDEX IF NOT EXISTS ix_district_boundaries_geometry_gist ON district_boundaries USING GIST (geometry)"))
        bind.execute(text("CREATE INDEX IF NOT EXISTS ix_candidate_interventions_geometry_gist ON candidate_interventions USING GIST (geometry)"))


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
