"""Versioned scenario computations; published scientific history is immutable."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, func, select

from app.core.config import settings
from app.models.entities import ForecastRun, IngestionJob, PipelineEvent, TemperatureObservation
from app.services.forecasting import evaluate_temperature_scenario
from app.services.locks import distributed_lock
from app.services.scientific_history import scientific_temperature_query, scientific_temperature_versions


class ForecastInProgress(Exception):
    pass


def forecast_inputs(db, area_ids=None):
    versions = scientific_temperature_versions(db)
    ids = [v.id for v in versions]
    query = scientific_temperature_query(db, area_ids, versions=versions)
    # Aggregate without loading all history on every cache lookup.
    subquery = query.subquery()
    count, last_observed = db.execute(select(func.count(), func.max(subquery.c.observed_at)).select_from(subquery)).one()
    context = {
        "dataset_version_ids": ids, "observation_count": count,
        "last_observed_at": last_observed.isoformat() if last_observed else None,
        "area_ids": sorted(set(area_ids or [])),
        "validation_policy": {
            "minimum_years": settings.forecast_min_years, "minimum_areas": settings.forecast_min_areas,
            "minimum_samples": settings.forecast_min_validation_samples,
            "minimum_improvement": settings.forecast_min_baseline_improvement,
        },
    }
    context["fingerprint"] = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    return {"history": context}, query


def compute_forecast(db, horizon_years, area_ids=None, *, frozen_inputs=None):
    inputs, query = frozen_inputs or forecast_inputs(db, area_ids)
    fingerprint = inputs["history"]["fingerprint"]

    def cached():
        return db.scalar(select(ForecastRun).where(
            ForecastRun.horizon_years == horizon_years, ForecastRun.model_version == "scenario-v3",
            ForecastRun.training_versions["history"]["fingerprint"].as_string() == fingerprint,
        ).order_by(desc(ForecastRun.created_at)).limit(1))

    found = cached()
    if found:
        return found, True
    with distributed_lock("forecast:%s:%s" % (horizon_years, fingerprint), timeout_seconds=660) as acquired:
        if not acquired:
            raise ForecastInProgress("A scenario for these inputs is already being calculated")
        found = cached()
        if found:
            return found, True
        rows = list(db.scalars(query.order_by(TemperatureObservation.observed_at, TemperatureObservation.id)))
        result = evaluate_temperature_scenario(rows, horizon_years)
        run = ForecastRun(
            horizon_years=horizon_years,
            method="validated-climatology-trend-scenario-v3" if result["status"] == "completed" else "guardrail-validation-v3",
            model_version="scenario-v3", training_versions=inputs,
            assumptions=result["assumptions"], metrics=result["metrics"], results=result,
        )
        db.add(run); db.commit()
        return run, False


def update_scheduled_forecasts(db):
    now = datetime.now(timezone.utc)
    with distributed_lock("forecast:scheduled-update", timeout_seconds=660) as acquired:
        if not acquired:
            return {"status": "locked"}
        inputs, query = forecast_inputs(db)
        if not inputs["history"]["observation_count"]:
            return {"status": "skipped", "reason": "no_published_scientific_history"}
        latest = db.scalar(select(IngestionJob).where(IngestionJob.job_type == "forecast_update").order_by(desc(IngestionJob.created_at)).limit(1))
        if latest:
            created = latest.created_at.replace(tzinfo=timezone.utc) if latest.created_at.tzinfo is None else latest.created_at
            if created > now - timedelta(days=1):
                return {"status": "skipped", "reason": "daily_compute_limit"}
            if latest.status == "completed" and latest.checkpoint.get("input_fingerprint") == inputs["history"]["fingerprint"]:
                return {"status": "skipped", "reason": "unchanged_training_inputs"}
        job = IngestionJob(job_type="forecast_update", status="running", stage="validation", requested_by="scheduler:model-update", started_at=now)
        db.add(job); db.commit()
        try:
            results = [compute_forecast(db, horizon, frozen_inputs=(inputs, query))[0] for horizon in (1, 3, 5)]
            job.status, job.stage, job.progress, job.finished_at = "completed", "completed", 1.0, datetime.now(timezone.utc)
            job.checkpoint = {"input_fingerprint": inputs["history"]["fingerprint"], "run_ids": [r.id for r in results]}
            version = (db.scalar(select(func.max(PipelineEvent.layer_version)).where(PipelineEvent.layer_key == "future-risk")) or 0) + 1
            db.add(PipelineEvent(event_type="layer.published", layer_key="future-risk", layer_version=version, payload={
                "affected_layers": ["future-risk"], "model_version": "scenario-v3", "forecast_run_ids": [r.id for r in results],
                "projection_available": all(r.results["status"] == "completed" for r in results),
            }))
            db.commit()
            return {"status": "completed", **job.checkpoint}
        except Exception:
            db.rollback()
            job.status, job.stage, job.finished_at = "failed", "failed", datetime.now(timezone.utc)
            job.error_message = "Scheduled scenario validation failed; prior model outputs remain unchanged"
            db.commit()
            raise
