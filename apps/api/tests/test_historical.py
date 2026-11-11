"""Isolated synthetic provider fixtures; never published to a real service."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import settings
from app.models.entities import DatasetVersion, GeographicArea, IngestionJob, SourceCheck, TemperatureObservation
from app.schemas.api import BackfillBudget, BackfillRequest
from app.services.connectors.base import AreaProcessingRecord, ConnectorError, ProviderRateLimitError, SourceAsset
from app.services.connectors.earth_engine import HistoricalPage, LandsatConnector
from app.services.historical import (
    BackfillUnavailable, cancel_backfill, create_backfill, dispatch_backfills, execute_backfill_step, resume_backfill,
)
from app.services.pipeline import run_monitor
from app.services.versions import published_version


EXTENT = {"type": "Polygon", "coordinates": [[[100.5, 13.7], [100.51, 13.7], [100.51, 13.71], [100.5, 13.71], [100.5, 13.7]]]}


def asset(name, year):
    return SourceAsset.with_checksum(name, name, datetime(year, 1, 2, tzinfo=timezone.utc), EXTENT,
                                    metadata={"data_class": "synthetic_test_fixture", "scientific_use": False})


class HistoricalFixture(LandsatConnector):
    produces_raster = False

    def __init__(self, assets=None):
        self.assets = assets if assets is not None else [asset("fixture-2020", 2020), asset("fixture-2021", 2021)]
        self.catalogue_calls = 0
        self.process_calls = 0
        self.process_error = None
        self.catalogue_error = None
        self.on_process = None

    def discover(self, latest_asset_id=None, checkpoint=None):
        return [asset("fixture-current", 2025)]

    def discover_historical_page(self, start_date, end_date, cursor, page_size=10, window_days=16, months=None):
        self.catalogue_calls += 1
        if self.catalogue_error:
            raise self.catalogue_error
        return HistoricalPage(self.assets, {"window_start": end_date.isoformat(), "collection_index": 0}, len(self.assets), True)

    def process(self, asset, areas):
        self.process_calls += 1
        if self.on_process:
            self.on_process()
        if self.process_error:
            raise self.process_error
        return [AreaProcessingRecord(area["id"], {"lst_c_mean": 35.0, "lst_c_p90": 39.0},
                coverage_fraction=.8, metadata={"fixture": True, "scientific_use": False}) for area in areas]


@pytest.fixture
def enabled(monkeypatch, db):
    monkeypatch.setattr(settings, "historical_ingestion_enabled", True)
    monkeypatch.setattr(settings, "gee_project", "synthetic-test-not-a-real-project")
    db.add(GeographicArea(id="fixture-area", name_th="พื้นที่ทดสอบ", name_en="Fixture", geometry={
        "type": "MultiPolygon", "coordinates": [EXTENT["coordinates"]]}))
    db.commit()


def request(**overrides):
    return BackfillRequest(**{"source": "landsat", "start_date": date(2020, 1, 1), "end_date": date(2022, 1, 1), **overrides})


def step(db, job, connector):
    db.refresh(job)
    checkpoint = deepcopy(job.checkpoint)
    checkpoint["next_step_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    checkpoint["dispatch_generation"] += 1
    checkpoint.pop("dispatch_until", None)
    job.checkpoint = checkpoint
    db.commit()
    return execute_backfill_step(db, job.id, checkpoint["dispatch_generation"], connector)


def test_history_publishes_in_steps_preserving_latest_and_source_check_time(db, enabled):
    fixture = HistoricalFixture()
    run_monitor(db, fixture, requested_by="synthetic-test")
    current = published_version(db, fixture.dataset_id)
    checked_at = db.scalar(select(SourceCheck.checked_at))
    job = create_backfill(db, request(), "test")
    assert step(db, job, fixture)["status"] == "waiting"
    assert len(db.scalars(select(TemperatureObservation)).all()) == 2
    assert step(db, job, fixture)["status"] == "completed"
    assert fixture.catalogue_calls == 1
    assert len(db.scalars(select(TemperatureObservation)).all()) == 3
    assert published_version(db, fixture.dataset_id).id == current.id
    assert db.scalar(select(SourceCheck.checked_at)) == checked_at
    assert len(db.scalars(select(SourceCheck)).all()) == 1
    assert execute_backfill_step(db, job.id, job.checkpoint["dispatch_generation"], fixture)["status"] == "duplicate_delivery"


def test_budget_stop_and_resume_keep_pending_assets_without_requery(db, enabled):
    fixture = HistoricalFixture()
    job = create_backfill(db, request(max_assets=1), "test")
    assert step(db, job, fixture)["status"] == "budget_exhausted"
    db.refresh(job)
    old_checkpoint = deepcopy(job.checkpoint)
    assert old_checkpoint["processing_attempts"] == 1 and len(old_checkpoint["pending"]) == 1
    resumed = resume_backfill(db, job.id, BackfillBudget(max_assets=2), "test")
    assert resumed.id != job.id and resumed.checkpoint["resumed_from_job_id"] == job.id
    assert step(db, resumed, fixture)["status"] == "completed"
    assert fixture.catalogue_calls == 1
    db.refresh(job)
    assert job.checkpoint == old_checkpoint


def test_retries_consume_budget_and_preserve_previous_validated_map(db, enabled):
    fixture = HistoricalFixture()
    run_monitor(db, fixture)
    current = published_version(db, fixture.dataset_id)
    fixture.process_error = ProviderRateLimitError("synthetic rate limit", 1200)
    job = create_backfill(db, request(max_assets=2), "test")
    assert step(db, job, fixture)["status"] == "retry_scheduled"
    db.refresh(job)
    assert job.checkpoint["processing_attempts"] == 1
    assert (datetime.fromisoformat(job.checkpoint["next_step_at"]) - datetime.now(timezone.utc)).total_seconds() > 1100
    assert step(db, job, fixture)["status"] == "failed"
    db.refresh(job)
    assert job.stage == "budget_exhausted" and job.checkpoint["processing_attempts"] == 2
    assert fixture.catalogue_calls == 1
    assert published_version(db, fixture.dataset_id).id == current.id
    assert len(db.scalars(select(TemperatureObservation)).all()) == 1


def test_catalogue_request_is_counted_before_failed_call(db, enabled):
    fixture = HistoricalFixture()
    fixture.catalogue_error = ConnectorError("synthetic catalogue outage")
    job = create_backfill(db, request(max_catalogue_requests=1), "test")
    assert step(db, job, fixture)["status"] == "failed"
    db.refresh(job)
    assert fixture.catalogue_calls == 1 and job.checkpoint["catalogue_requests"] == 1
    assert job.checkpoint["cursor"] == {}


def test_duplicate_and_rejected_assets_advance_without_reprocessing(db, enabled):
    fixture = HistoricalFixture()
    run_monitor(db, fixture)
    fixture.assets = [asset("fixture-current", 2025), asset("fixture-invalid", 2020), asset("fixture-good", 2021)]
    original_validate = fixture.validate
    fixture.validate = lambda item: {"passed": False, "errors": ["synthetic cloud rejection"]} if "invalid" in item.source_asset_id else original_validate(item)
    job = create_backfill(db, request(end_date=date(2026, 1, 1)), "test")
    assert step(db, job, fixture)["status"] == "waiting"
    assert step(db, job, fixture)["status"] == "completed"
    assert fixture.process_calls == 2  # current + good; rejected image never aggregated
    db.refresh(job)
    assert job.checkpoint["skipped"] == 1
    assert db.scalar(select(DatasetVersion).where(DatasetVersion.source_asset_id == "fixture-invalid")).quality_status == "rejected"


def test_cancel_during_atomic_asset_publication_stops_remaining_work(db, enabled):
    fixture = HistoricalFixture()
    job = create_backfill(db, request(), "test")
    fixture.on_process = lambda: cancel_backfill(db, job.id)
    step(db, job, fixture)
    db.refresh(job)
    assert job.status == "completed" and job.stage == "cancelled"
    assert len(db.scalars(select(TemperatureObservation)).all()) == 1
    assert len(job.checkpoint["pending"]) == 1


def test_active_job_conflict_and_queued_cancel(db, enabled):
    job = create_backfill(db, request(), "test")
    with pytest.raises(BackfillUnavailable, match="มีงานย้อนหลัง"):
        create_backfill(db, request(), "test")
    with pytest.raises(BackfillUnavailable):
        resume_backfill(db, job.id, BackfillBudget(), "test")
    cancel_backfill(db, job.id)
    assert job.stage == "cancelled"
    assert execute_backfill_step(db, job.id, 0, HistoricalFixture())["status"] == "duplicate_delivery"


def test_dispatch_generation_prevents_late_redelivery_and_recovers_crash(db, enabled):
    fixture = HistoricalFixture()
    job = create_backfill(db, request(), "test")
    deliveries = []
    assert dispatch_backfills(db, lambda *args: deliveries.append(args))["dispatched"] == 1
    assert dispatch_backfills(db, lambda *args: deliveries.append(args))["dispatched"] == 0
    assert execute_backfill_step(db, *deliveries[0], fixture)["status"] == "waiting"
    db.refresh(job)
    cp = deepcopy(job.checkpoint)
    cp["next_step_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    job.checkpoint = cp
    db.commit()
    assert execute_backfill_step(db, *deliveries[0], fixture)["status"] == "duplicate_delivery"
    # Simulate a worker loss after claiming a subsequent delivery.
    cp["running_until"] = cp["next_step_at"]
    job.checkpoint, job.status = cp, "running"
    db.commit()
    assert dispatch_backfills(db, lambda *args: deliveries.append(args))["dispatched"] == 1
    assert execute_backfill_step(db, *deliveries[-1], fixture)["status"] == "completed"
    assert fixture.catalogue_calls == 1


def test_disabled_flag_and_auth_fail_without_provider_calls(client, db, monkeypatch):
    monkeypatch.setattr(settings, "historical_ingestion_enabled", False)
    assert client.get("/api/ingestion/backfills").json()["enabled"] is False
    body = request().model_dump(mode="json")
    assert client.post("/api/ingestion/backfills", json=body).status_code in (401, 403)
    result = client.post("/api/ingestion/backfills", json=body, headers={"X-Admin-Key": "test-admin-key"})
    assert result.status_code == 409 and "Earth Engine" in result.json()["detail"]
    assert not db.scalars(select(IngestionJob)).all()
    assert dispatch_backfills(db, MagicMock())["status"] == "disabled"


def test_backfill_bounds_reject_future_dates_and_unbounded_work():
    for change in [{"max_assets": 101}, {"max_catalogue_requests": 501}, {"step_delay_seconds": 1}, {"page_size": 26}]:
        with pytest.raises(ValidationError):
            request(**change)
    with pytest.raises(ValidationError):
        BackfillRequest(source="landsat", start_date="2000-01-01", end_date="2025-01-01")
    with pytest.raises(ValidationError):
        BackfillRequest(source="landsat", start_date="2020-01-01", end_date=date.today() + timedelta(days=2))


def test_real_connector_keyset_pages_keep_original_ids_and_visit_both_collections(monkeypatch):
    connector = LandsatConnector()
    query_log = []
    all_features = {key: [] for key in connector.collection_ids}
    for collection_id in connector.collection_ids:
        for index in ("001", "002", "003"):
            all_features[collection_id].append({"id": collection_id + "/" + index,
                "properties": {"system:index": index, "system:time_start": 1577923200000},
                "bands": [{"id": band} for band in connector.required_bands] if index != "002" else []})

    class Collection:
        def __init__(self, collection_id):
            self.collection_id, self.after, self.size = collection_id, "", 0
        def filterBounds(self, value): return self
        def filterDate(self, start, end): return self
        def filter(self, value):
            if value[0] == "gt": self.after = value[2]
            return self
        def sort(self, key, ascending):
            assert key == "system:index" and ascending
            return self
        def limit(self, size):
            self.size = size
            return self
        def getInfo(self):
            query_log.append((self.collection_id, self.after, self.size))
            return {"features": [item for item in all_features[self.collection_id]
                    if item["properties"]["system:index"] > self.after][:self.size]}

    ee = SimpleNamespace(ImageCollection=Collection, Geometry=SimpleNamespace(Rectangle=lambda value: value),
                         Filter=SimpleNamespace(gt=lambda *v: ("gt", *v), lte=lambda *v: ("lte", *v)))
    monkeypatch.setattr(connector, "_earth_engine", lambda: ee)
    cursor, found, done = {}, [], False
    while not done:
        page = connector.discover_historical_page(date(2020, 1, 1), date(2020, 1, 4), cursor, page_size=2)
        found.extend(item.source_asset_id for item in page.assets)
        cursor, done = page.cursor, page.done
        assert len(query_log) <= 4
    assert found == [key + "/" + index for key in connector.collection_ids for index in ("001", "003")]
    assert len(query_log) == 4 and all(size == 3 for _, _, size in query_log)


def test_month_filter_scans_only_dry_season_windows(monkeypatch):
    connector = LandsatConnector()
    windows = []

    class Collection:
        def __init__(self, collection_id): pass
        def filterBounds(self, value): return self
        def filterDate(self, start, end):
            windows.append((start, end))
            return self
        def filter(self, value): return self
        def sort(self, key, ascending): return self
        def limit(self, size): return self
        def getInfo(self): return {"features": []}

    ee = SimpleNamespace(ImageCollection=Collection, Geometry=SimpleNamespace(Rectangle=lambda value: value),
                         Filter=SimpleNamespace(gt=lambda *v: ("gt", *v), lte=lambda *v: ("lte", *v)))
    monkeypatch.setattr(connector, "_earth_engine", lambda: ee)
    cursor, done = {}, False
    while not done:
        page = connector.discover_historical_page(date(2022, 3, 20), date(2023, 2, 1), cursor, window_days=16, months=[1, 2, 3, 4])
        cursor, done = page.cursor, page.done
    months = {int(start[5:7]) for start, _ in windows}
    assert months <= {1, 2, 3, 4} and min(start for start, _ in windows) == "2022-03-20"
    assert all(int(end[5:7]) in {1, 2, 3, 4, 5} for _, end in windows)
    assert any(start.startswith("2023-01") for start, _ in windows)
    with pytest.raises(ValueError):
        from app.schemas.api import BackfillRequest
        BackfillRequest(source="landsat", start_date=date(2022, 1, 1), end_date=date(2023, 1, 1), months=[13])
