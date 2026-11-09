from datetime import datetime, timezone
from unittest.mock import Mock

from sqlalchemy import select

import pytest

from app.core.config import settings
from app.models.entities import (
    AreaIndicator,
    DataSource,
    DatasetVersion,
    GeographicArea,
    IngestionJob,
    OptimizationRun,
    PriorityScore,
    PipelineEvent,
    RasterAssetMetadata,
    SourceCheck,
    TemperatureObservation,
)
from app.services.connectors.base import AreaProcessingRecord, ConnectorError, SourceAsset, SourceConnector
from app.services.connectors.earth_engine import LandsatConnector, SentinelConnector
from app.services.connectors.open_meteo import OpenMeteoConnector
from app.services.pipeline import run_monitor


EXTENT = {"type": "Point", "coordinates": [100.5, 13.7]}


def test_earth_engine_uses_scoped_adc_when_credential_file_is_configured(monkeypatch):
    import ee
    import google.auth

    credentials = object()
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/run/secrets/test-earth-engine.json")
    monkeypatch.setattr(settings, "gee_project", "test-ee-project")

    def get_credentials(*, scopes):
        assert scopes == ["https://www.googleapis.com/auth/earthengine"]
        return credentials, "test-ee-project"

    initialized = []
    monkeypatch.setattr(google.auth, "default", get_credentials)
    monkeypatch.setattr(ee, "Initialize", lambda **kwargs: initialized.append(kwargs))
    assert LandsatConnector()._earth_engine() is ee
    assert initialized == [{"credentials": credentials, "project": "test-ee-project"}]


@pytest.mark.parametrize("missing_stats", [False, True])
def test_earth_engine_reduces_coverage_then_statistics_only_for_eligible_districts(monkeypatch, missing_stats):
    ee, image, pixel_area = Mock(), Mock(), Mock()
    ee.Feature.side_effect = lambda geometry, properties: properties
    ee.FeatureCollection.side_effect = lambda features: features
    ee.Image.pixelArea.return_value.updateMask.return_value = pixel_area
    connector = SentinelConnector()
    monkeypatch.setattr(connector, "_earth_engine", lambda: ee)
    monkeypatch.setattr(connector, "_analysis", lambda *args: {"image": image, "mask": "clear", "reducer": "mean"})
    asset = SourceAsset.with_checksum("test-scene", "1", datetime(2026, 1, 1, tzinfo=timezone.utc), EXTENT)
    districts = [{"id": name, "geometry": EXTENT} for name in ("clear", "cloudy")]

    def coverage_response():
        assert not image.reduceRegions.called
        return {"features": [{"properties": {"area_id": name, "coverage_fraction": fraction}}
                             for name, fraction in (("clear", 0.2), ("cloudy", 0.01))]}

    pixel_area.reduceRegions.return_value.map.return_value.getInfo.side_effect = coverage_response
    image.reduceRegions.return_value.map.return_value.getInfo.return_value = {
        "features": [] if missing_stats else [{"properties": {"area_id": "clear", "ndvi": 0.42}}],
    }
    if missing_stats:
        with pytest.raises(ConnectorError, match="one row per requested district"):
            connector.process(asset, districts)
    else:
        record, = connector.process(asset, districts)
        assert record.area_id == "clear" and record.indicators == {"ndvi": 0.42}
        assert record.coverage_fraction == 0.2 and record.quality_status == "limited_coverage"
    assert pixel_area.reduceRegions.call_args.kwargs["collection"] == [{"area_id": "clear"}, {"area_id": "cloudy"}]
    assert image.reduceRegions.call_args.kwargs["collection"] == [{"area_id": "clear"}]
    assert image.reduceRegions.call_args.kwargs["scale"] == 10


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


class ProcessedTemperatureConnector(SourceConnector):
    source_name = "Landsat 8/9"
    dataset_id = "LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2"
    layer_key = "temperature"
    requires_area_processing = True

    def __init__(self, fail_once=False):
        self.fail_once = fail_once
        self.process_calls = 0

    def discover(self, latest_asset_id=None, checkpoint=None):
        if latest_asset_id == "landsat-real-1":
            return []
        return [
            SourceAsset.with_checksum(
                "landsat-real-1",
                "20260101",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                EXTENT,
            )
        ]

    def process(self, asset, areas):
        self.process_calls += 1
        if self.fail_once and self.process_calls == 1:
            raise ConnectorError("temporary Earth Engine timeout")
        values = {"area-a": (38.0, 42.0), "area-b": (34.0, 37.0)}
        return [
            AreaProcessingRecord(
                area_id=area["id"],
                indicators={"lst_c_mean": values[area["id"]][0], "lst_c_p90": values[area["id"]][1]},
                units={"lst_c_mean": "°C", "lst_c_p90": "°C"},
                coverage_fraction=0.8,
                metadata={"aggregation": "test regional reduction"},
            )
            for area in areas
        ]


class RasterTemperatureConnector(ProcessedTemperatureConnector):
    produces_raster = True

    def raster_metadata(self, asset):
        return {
            "storage_uri": "earthengine://%s" % asset.source_asset_id,
            "media_type": "application/vnd.google-earth-engine.image",
            "crs": "EPSG:4326",
            "bounds": asset.geographic_extent,
            "resolution_m": 30,
            "band_metadata": {"source_checksum": asset.checksum, "processor_version": "test-v1"},
        }


def add_test_areas(db):
    for area_id, offset in (("area-a", 0.0), ("area-b", 0.05)):
        db.add(
            GeographicArea(
                id=area_id,
                name_th=area_id,
                name_en=area_id,
                geometry={
                    "type": "MultiPolygon",
                    "coordinates": [[[[100.5 + offset, 13.7], [100.51 + offset, 13.7], [100.51 + offset, 13.71], [100.5 + offset, 13.71], [100.5 + offset, 13.7]]]],
                },
            )
        )
    db.commit()


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


def test_processed_satellite_records_publish_atomically_and_recalculate_priority(db, client):
    add_test_areas(db)
    result = run_monitor(db, ProcessedTemperatureConnector(), requested_by="test")

    assert result["processed"] == 1
    observations = db.scalars(select(TemperatureObservation).order_by(TemperatureObservation.area_id)).all()
    indicators = db.scalars(select(AreaIndicator).where(AreaIndicator.indicator_key == "lst_c_mean")).all()
    scores = db.scalars(select(PriorityScore).order_by(PriorityScore.area_id)).all()
    version = db.scalar(select(DatasetVersion).where(DatasetVersion.source_asset_id == "landsat-real-1"))

    assert version.status == "published"
    assert version.quality_summary["processed_areas"] == 2
    assert len(observations) == 2
    assert len(indicators) == 2
    assert len(scores) == 2
    assert scores[0].components["temperature_anomaly"] == 2.0
    assert scores[1].components["temperature_anomaly"] == -2.0
    event = db.scalar(
        select(PipelineEvent).where(PipelineEvent.dataset_version_id == version.id)
    )
    assert event.event_type == "layer.published"
    assert event.layer_version == version.layer_version
    assert event.payload["affected_layers"] == ["temperature", "historical", "priority"]

    map_response = client.get(
        "/api/map/features?layer=temperature&bbox=100.49,13.69,100.58,13.77&layer_version=%s"
        % version.layer_version
    )
    assert map_response.status_code == 200
    assert map_response.json()["metadata"]["requested_layer_version"] == version.layer_version
    assert sorted(feature["properties"]["value"] for feature in map_response.json()["features"]) == [34.0, 38.0]


def test_raster_provenance_is_published_with_virtual_provider_uri(db):
    add_test_areas(db)
    run_monitor(db, RasterTemperatureConnector(), requested_by="test")

    version = db.scalar(select(DatasetVersion).where(DatasetVersion.source_asset_id == "landsat-real-1"))
    raster = db.scalar(
        select(RasterAssetMetadata).where(RasterAssetMetadata.dataset_version_id == version.id)
    )
    assert raster.storage_uri == "earthengine://landsat-real-1"
    assert raster.media_type == "application/vnd.google-earth-engine.image"
    assert raster.resolution_m == 30
    assert raster.band_metadata["source_checksum"] == version.checksum


def test_processing_error_preserves_retryable_version_and_succeeds_on_retry(db):
    add_test_areas(db)
    connector = ProcessedTemperatureConnector(fail_once=True)

    with pytest.raises(ConnectorError, match="temporary Earth Engine timeout"):
        run_monitor(db, connector, requested_by="test")

    failed = db.scalar(select(DatasetVersion).where(DatasetVersion.source_asset_id == "landsat-real-1"))
    assert failed.status == "failed"
    assert failed.quality_status == "processing_error"
    assert db.scalar(select(AreaIndicator).where(AreaIndicator.dataset_version_id == failed.id)) is None

    retried = run_monitor(db, connector, requested_by="test-retry")
    db.refresh(failed)
    assert retried["processed"] == 1
    assert failed.status == "published"
    assert connector.process_calls == 2
    assert len(db.scalars(select(TemperatureObservation)).all()) == 2


@pytest.mark.parametrize("stage", ["staged", "validating", "processing", "publishing"])
def test_next_exclusive_monitor_recovers_interrupted_versions_without_duplicate_publication(db, stage):
    run_monitor(db, StaticConnector(), requested_by="test")
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == StaticConnector.dataset_id))
    interrupted = DatasetVersion(
        source_id=source.id, source_asset_id="asset-2", source_version="v2",
        observed_at=datetime(2026, 3, 1, tzinfo=timezone.utc), status=stage, quality_status="pending",
    )
    db.add(interrupted)
    db.flush()
    previous_job = IngestionJob(source_id=source.id, dataset_version_id=interrupted.id, status="running", stage=stage)
    previous_check = SourceCheck(source_id=source.id, status="running")
    db.add_all([previous_job, previous_check])
    db.commit()

    result = run_monitor(db, UpdatedConnector(), requested_by="worker-recovery-test")
    db.refresh(interrupted)
    db.refresh(previous_job)
    db.refresh(previous_check)
    assert result["processed"] == 1
    assert interrupted.status == "published"
    assert interrupted.layer_version == 2
    assert previous_job.status == "failed"
    assert previous_job.stage == "interrupted"
    assert previous_check.status == "failed"
    assert len(db.scalars(select(DatasetVersion)).all()) == 2
    assert len(db.scalars(select(PipelineEvent)).all()) == 2
    assert run_monitor(db, UpdatedConnector())["processed"] == 0
    assert len(db.scalars(select(PipelineEvent)).all()) == 2


def test_publication_failure_rolls_back_observations_and_can_recover_on_next_monitor(db, monkeypatch):
    from app.services import pipeline

    add_test_areas(db)
    persist_metadata = pipeline._persist_raster_metadata
    def unavailable_store(*args):
        raise ConnectorError("raster store temporarily down")

    monkeypatch.setattr(pipeline, "_persist_raster_metadata", unavailable_store)
    with pytest.raises(ConnectorError, match="raster store"):
        run_monitor(db, RasterTemperatureConnector())
    assert db.scalars(select(TemperatureObservation)).all() == []
    assert db.scalars(select(PipelineEvent)).all() == []
    monkeypatch.setattr(pipeline, "_persist_raster_metadata", persist_metadata)
    assert run_monitor(db, RasterTemperatureConnector())["processed"] == 1
    assert len(db.scalars(select(TemperatureObservation)).all()) == 2
    assert len(db.scalars(select(PipelineEvent)).all()) == 1


def test_priority_recalculation_excludes_synthetic_inputs_from_real_observations(db):
    add_test_areas(db)
    db.add(AreaIndicator(area_id="area-a", indicator_key="population_density", value=999_999, observed_at=datetime.now(timezone.utc), quality_status="synthetic_demo"))
    db.commit()
    run_monitor(db, ProcessedTemperatureConnector())
    score = db.scalar(select(PriorityScore).where(PriorityScore.area_id == "area-a"))
    assert score.components["population_exposure"] is None
    assert "population_exposure" in score.missing_inputs


def test_single_band_reductions_pin_property_names(monkeypatch):
    """reduceRegions names single-band outputs after the reducer, not the band."""
    from app.services.connectors.earth_engine import LandsatConnector

    ee, image = Mock(), Mock()
    ee.Feature.side_effect = lambda geometry, properties: properties
    ee.FeatureCollection.side_effect = lambda features: features
    pixel_area = ee.Image.pixelArea.return_value.updateMask.return_value
    pixel_area.reduceRegions.return_value.map.return_value.getInfo.return_value = {
        "features": [{"properties": {"area_id": "a", "coverage_fraction": 0.9}}]}
    image.reduceRegions.return_value.map.return_value.getInfo.return_value = {
        "features": [{"properties": {"area_id": "a", "lst_c_mean": 36.0, "lst_c_p90": 39.0}}]}
    reducer = Mock()
    for connector, outputs in ((SentinelConnector(), ["ndvi"]), (LandsatConnector(), ["lst_c_mean", "lst_c_p90"])):
        monkeypatch.setattr(connector, "_earth_engine", lambda: ee)
        analysis = {"image": image, "mask": "m", "reducer": reducer, "outputs": outputs}
        monkeypatch.setattr(connector, "_analysis", lambda *args, a=analysis: a)
        asset = SourceAsset.with_checksum("scene", "1", datetime(2026, 1, 1, tzinfo=timezone.utc), EXTENT)
        connector.process(asset, [{"id": "a", "geometry": EXTENT}])
        reducer.setOutputs.assert_called_with(outputs)
        assert image.reduceRegions.call_args.kwargs["reducer"] is reducer.setOutputs.return_value
    ee.Reducer.sum.return_value.setOutputs.assert_called_with(["area"])
    assert pixel_area.reduceRegions.call_args.kwargs["reducer"] is ee.Reducer.sum.return_value.setOutputs.return_value
