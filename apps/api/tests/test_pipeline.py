from datetime import datetime, timezone

from sqlalchemy import select

from app.models.entities import DataSource, DatasetVersion, OptimizationRun
from app.services.connectors.base import SourceAsset, SourceConnector
from app.services.connectors.open_meteo import OpenMeteoConnector
from app.services.pipeline import run_monitor


EXTENT = {"type": "Point", "coordinates": [100.5, 13.7]}


class StaticConnector(SourceConnector):
    source_name = "Open-Meteo"
    dataset_id = "open-meteo/weather-context"
    layer_key = "weather"

    def discover(self, latest_asset_id=None, checkpoint=None):
        if latest_asset_id == "asset-1":
            return []
        return [
            SourceAsset.with_checksum(
                "asset-1",
                "v1",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                EXTENT,
                metadata={"data_class": "test"},
                payload={"marker": "validated"},
            )
        ]


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
    assert versions[0].metadata_json["provider_payload"] == {"marker": "validated"}


def test_open_meteo_validation_requires_complete_current_and_hourly_payload():
    connector = OpenMeteoConnector()
    valid = SourceAsset.with_checksum(
        "weather-1",
        "v1",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        EXTENT,
        metadata={"data_class": "model_estimate_and_forecast"},
        payload={
            "current": {
                "time": "2026-01-01T00:00",
                "temperature_2m": 31.4,
                "relative_humidity_2m": 62,
                "apparent_temperature": 36.1,
                "wind_speed_10m": 8.2,
            },
            "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
            "hourly": {
                "time": ["2026-01-01T00:00", "2026-01-01T01:00"],
                "temperature_2m": [31.4, 30.9],
                "apparent_temperature": [36.1, 35.2],
                "relative_humidity_2m": [62, 65],
                "precipitation_probability": [10, 20],
            },
            "hourly_units": {"temperature_2m": "°C", "precipitation_probability": "%"},
        },
    )
    report = connector.validate(valid)
    assert report["passed"] is True
    assert report["metrics"]["forecast_hours"] == 2

    invalid = SourceAsset.with_checksum(
        "weather-2",
        "v1",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        EXTENT,
        payload={"current": {"time": "2026-01-01T00:00"}},
    )
    rejected = connector.validate(invalid)
    assert rejected["passed"] is False
    assert any("current fields" in error for error in rejected["errors"])
    assert any("hourly fields" in error for error in rejected["errors"])


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
