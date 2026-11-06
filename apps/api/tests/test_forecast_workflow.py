from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.config import settings
from app.models.entities import DataSource, DatasetVersion, ForecastRun, GeographicArea, IngestionJob, TemperatureObservation
from app.services.forecast_workflow import forecast_inputs, update_scheduled_forecasts


def _history(db, status="published", quality="valid", metadata=None, asset="history"):
    source = db.scalar(select(DataSource).where(DataSource.dataset_id.like("LANDSAT/%")))
    area = db.get(GeographicArea, "workflow-area")
    if area is None:
        db.add(GeographicArea(id="workflow-area", name_th="พื้นที่ทดสอบ", name_en="Synthetic test district", geometry={"type": "Point", "coordinates": [100.5, 13.7]}))
    version = DatasetVersion(source_id=source.id, source_asset_id=asset, source_version=asset, status=status, quality_status=quality, metadata_json=metadata or {}, observed_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    db.add(version); db.flush()
    db.add(TemperatureObservation(area_id="workflow-area", dataset_version_id=version.id, observed_at=datetime(2020, 1, 1, tzinfo=timezone.utc), lst_c_mean=32, coverage_fraction=.9))
    db.commit()
    return version


def test_forecast_reuses_recorded_results_for_the_same_inputs_and_policy(client, db, monkeypatch):
    first = client.post("/api/forecast/run", json={"horizon_years": 5}).json()
    second = client.post("/api/forecast/run", json={"horizon_years": 5}).json()
    assert first["cached"] is False and second["cached"] is True
    assert first["run_id"] == second["run_id"]
    assert db.scalar(select(func.count()).select_from(ForecastRun)) == 1
    monkeypatch.setattr(settings, "forecast_min_years", settings.forecast_min_years + 1)
    third = client.post("/api/forecast/run", json={"horizon_years": 5}).json()
    assert third["cached"] is False and third["run_id"] != first["run_id"]


def test_staged_and_synthetic_history_never_enters_scientific_training(db):
    from app.services.analytics import _temperature_trend
    _history(db, status="processing", asset="not-published")
    _history(db, quality="synthetic_demo", asset="synthetic")
    _history(db, metadata={"fixture": True, "scientific_use": False}, asset="marked-fixture")
    context, _ = forecast_inputs(db)
    assert context["history"]["observation_count"] == 0
    assert context["history"]["dataset_version_ids"] == []
    assert _temperature_trend(db, "workflow-area") is None


def test_a_new_historical_backfill_invalidates_the_model_cache(client, db):
    first = _history(db, asset="first-history")
    old = client.post("/api/forecast/run", json={"horizon_years": 3}).json()
    backfill = _history(db, asset="older-backfill")
    new = client.post("/api/forecast/run", json={"horizon_years": 3}).json()
    assert new["cached"] is False and new["run_id"] != old["run_id"]
    assert set(new["training_versions"]["history"]["dataset_version_ids"]) == {first.id, backfill.id}
    assert db.get(ForecastRun, old["run_id"]).training_versions == old["training_versions"]


def test_scheduled_model_update_is_bounded_and_uses_the_same_history_for_all_horizons(db):
    assert update_scheduled_forecasts(db)["reason"] == "no_published_scientific_history"
    _history(db)
    result = update_scheduled_forecasts(db)
    assert result["status"] == "completed" and len(result["run_ids"]) == 3
    runs = [db.get(ForecastRun, run_id) for run_id in result["run_ids"]]
    assert {run.horizon_years for run in runs} == {1, 3, 5}
    assert all(run.results["status"] == "insufficient_data" for run in runs)
    assert len({run.training_versions["history"]["fingerprint"] for run in runs}) == 1
    assert update_scheduled_forecasts(db)["reason"] == "daily_compute_limit"
    job = db.scalar(select(IngestionJob).where(IngestionJob.job_type == "forecast_update"))
    job.created_at = datetime.now(timezone.utc) - timedelta(days=2); db.commit()
    assert update_scheduled_forecasts(db)["reason"] == "unchanged_training_inputs"
