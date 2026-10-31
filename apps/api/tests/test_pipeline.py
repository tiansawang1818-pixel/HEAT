from datetime import datetime, timezone

from sqlalchemy import select

from app.models.entities import DataSource, DatasetVersion, OptimizationRun
from app.services.connectors.base import SourceAsset, SourceConnector
from app.services.pipeline import run_monitor


EXTENT = {"type": "Point", "coordinates": [100.5, 13.7]}


class StaticConnector(SourceConnector):
    source_name = "Open-Meteo"
    dataset_id = "open-meteo/weather-context"
    layer_key = "weather"

    def discover(self, latest_asset_id=None, checkpoint=None):
        if latest_asset_id == "asset-1":
            return []
        return [SourceAsset.with_checksum("asset-1", "v1", datetime(2026, 1, 1, tzinfo=timezone.utc), EXTENT)]


class InvalidConnector(StaticConnector):
    def discover(self, latest_asset_id=None, checkpoint=None):
        return [SourceAsset.with_checksum("bad-asset", "v2", datetime(2026, 2, 1, tzinfo=timezone.utc), EXTENT)]

    def validate(self, asset):
        return {"passed": False, "errors": ["cloud coverage too high"], "warnings": [], "metrics": {"coverage": 0.01}}


class UpdatedConnector(StaticConnector):
    def discover(self, latest_asset_id=None, checkpoint=None):
        if latest_asset_id == "asset-2":
            return []
        return [SourceAsset.with_checksum("asset-2", "v2", datetime(2026, 3, 1, tzinfo=timezone.utc), EXTENT)]


def test_new_asset_publishes_and_duplicate_is_skipped(db):
    first = run_monitor(db, StaticConnector(), requested_by="test")
    second = run_monitor(db, StaticConnector(), requested_by="test")
    assert first["processed"] == 1
    assert second["processed"] == 0
    versions = db.scalars(select(DatasetVersion)).all()
    assert len(versions) == 1
    assert versions[0].status == "published"
    assert versions[0].layer_version == 1


def test_invalid_asset_never_replaces_published_version(db):
    run_monitor(db, StaticConnector(), requested_by="test")
    run_monitor(db, InvalidConnector(), requested_by="test")
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == StaticConnector.dataset_id))
    published = db.scalars(
        select(DatasetVersion).where(DatasetVersion.source_id == source.id, DatasetVersion.status == "published")
    ).all()
    rejected = db.scalar(select(DatasetVersion).where(DatasetVersion.source_asset_id == "bad-asset"))
    assert len(published) == 1
    assert rejected.status == "failed"
    assert rejected.quality_status == "rejected"


def test_new_publication_marks_saved_optimization_outdated(db):
    run_monitor(db, StaticConnector(), requested_by="test")
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == StaticConnector.dataset_id))
    first_version = db.scalar(
        select(DatasetVersion).where(DatasetVersion.source_id == source.id, DatasetVersion.status == "published")
    )
    optimization = OptimizationRun(
        budget_thb=1_000_000,
        allocated_thb=0,
        objective="balanced",
        objective_weights={"priority": 1.0},
        objective_value=0,
        solver="OR-Tools CP-SAT",
        input_versions={
            source.dataset_id: {
                "version_id": first_version.id,
                "source_version": first_version.source_version,
                "layer_version": first_version.layer_version,
            }
        },
    )
    optimization_without_source = OptimizationRun(
        budget_thb=1_000_000,
        allocated_thb=0,
        objective="balanced",
        objective_weights={"priority": 1.0},
        objective_value=0,
        solver="OR-Tools CP-SAT",
        input_versions={},
    )
    db.add_all([optimization, optimization_without_source])
    db.commit()

    run_monitor(db, UpdatedConnector(), requested_by="test")
    db.refresh(optimization)
    db.refresh(optimization_without_source)
    assert optimization.is_outdated is True
    assert optimization_without_source.is_outdated is True
