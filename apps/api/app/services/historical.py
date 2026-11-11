"""Bounded, resumable historical ingestion. No provider call runs in an HTTP request."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Callable

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import DataSource, DatasetVersion, IngestionJob
from app.schemas.api import BackfillBudget, BackfillRequest
from app.services.connectors.base import ConnectorError, ProviderRateLimitError, SourceAsset
from app.services.connectors.earth_engine import EarthEngineConnector
from app.services.locks import MONITOR_LOCK_LEASE_SECONDS, distributed_lock
from app.services.pipeline import _recover_interrupted_source, ingest_asset
from app.services.source_registry import CONNECTOR_TYPES


JOB_TYPE = "historical_backfill"
ACTIVE = ("queued", "running", "cancel_requested")


class BackfillUnavailable(ValueError):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def aware(value: str):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def require_enabled():
    if not settings.historical_ingestion_enabled or not settings.gee_project:
        raise BackfillUnavailable("ยังปิดการนำเข้าย้อนหลัง: ต้องตรวจสิทธิ์และโควตา Earth Engine ตั้ง GEE_PROJECT และเปิด HISTORICAL_INGESTION_ENABLED ก่อน")


def create_backfill(db: Session, request: BackfillRequest, actor: str, previous: IngestionJob = None):
    require_enabled()
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == CONNECTOR_TYPES[request.source].dataset_id))
    if source is None or not source.enabled:
        raise BackfillUnavailable("แหล่งข้อมูลยังไม่พร้อมใช้งาน")
    with distributed_lock("historical-create:%s" % source.id, timeout_seconds=60) as acquired:
        if not acquired:
            raise BackfillUnavailable("กำลังสร้างงานสำหรับแหล่งข้อมูลนี้ กรุณาลองใหม่")
        active = db.scalar(select(IngestionJob).where(IngestionJob.source_id == source.id,
                           IngestionJob.job_type == JOB_TYPE, IngestionJob.status.in_(ACTIVE)).limit(1))
        if active:
            raise BackfillUnavailable("มีงานย้อนหลังสำหรับแหล่งนี้อยู่แล้ว: %s" % active.id)
        old = previous.checkpoint if previous else {}
        checkpoint = {
            "schema_version": 1, "configuration": request.model_dump(mode="json"),
            "cursor": deepcopy(old.get("cursor", {})), "pending": deepcopy(old.get("pending", [])),
            "catalogue_done": old.get("catalogue_done", False), "catalogue_requests": 0,
            "processing_attempts": 0, "skipped": 0, "scanned": 0, "consecutive_errors": 0,
            "dispatch_generation": 0, "next_step_at": utcnow().isoformat(),
            "resumed_from_job_id": previous.id if previous else None,
        }
        job = IngestionJob(source_id=source.id, job_type=JOB_TYPE, requested_by=actor[:80],
                           checkpoint=checkpoint, status="queued", stage="queued")
        db.add(job)
        db.commit()
        return job


def resume_backfill(db: Session, job_id: str, budget: BackfillBudget, actor: str):
    previous = db.get(IngestionJob, job_id)
    if not previous or previous.job_type != JOB_TYPE:
        raise LookupError("ไม่พบงานย้อนหลัง")
    if previous.status in ACTIVE or previous.stage == "completed":
        raise BackfillUnavailable("งานยังทำอยู่หรือค้นช่วงเวลานี้ครบแล้ว ไม่ต้องสร้างงานซ้ำ")
    definition = {**previous.checkpoint["configuration"], **budget.model_dump()}
    return create_backfill(db, BackfillRequest.model_validate(definition), actor, previous)


def cancel_backfill(db: Session, job_id: str):
    job = db.get(IngestionJob, job_id)
    if not job or job.job_type != JOB_TYPE:
        raise LookupError("ไม่พบงานย้อนหลัง")
    # Conditional writes cannot overwrite a worker's concurrent completion.
    db.execute(update(IngestionJob).where(IngestionJob.id == job_id, IngestionJob.status == "queued").values(
        status="completed", stage="cancelled", finished_at=utcnow()))
    db.execute(update(IngestionJob).where(IngestionJob.id == job_id, IngestionJob.status == "running").values(
        status="cancel_requested", stage="cancel_requested"))
    db.commit()
    db.refresh(job)
    return job


def _store(db, job_id, checkpoint, status="queued", stage="waiting", error=None):
    """An API cancellation wins over all worker progress/error writes."""
    terminal = status in ("completed", "failed")
    result = db.execute(update(IngestionJob).where(IngestionJob.id == job_id,
        IngestionJob.status.in_(("running", "queued"))).values(
            checkpoint=checkpoint, status=status, stage=stage, error_message=error,
            finished_at=utcnow() if terminal else None,
            progress=1.0 if terminal and stage == "completed" else min(.99, checkpoint["processing_attempts"] /
                checkpoint["configuration"]["max_assets"])))
    if not result.rowcount:
        db.execute(update(IngestionJob).where(IngestionJob.id == job_id,
            IngestionJob.status == "cancel_requested").values(
                checkpoint=checkpoint, status="completed", stage="cancelled", finished_at=utcnow()))
    db.commit()
    return db.get(IngestionJob, job_id)


def _serialize(asset):
    raw = asdict(asset)
    raw["observed_at"] = asset.observed_at.isoformat()
    return raw


def _asset(raw):
    return SourceAsset(**{**raw, "observed_at": aware(raw["observed_at"])})


def execute_backfill_step(db: Session, job_id: str, generation: int, connector=None):
    """At most one catalogue request and one aggregation attempt per delivery.

    Request counters commit *before* provider calls. Retries consume the same
    finite budget; no automatic budget increases or changes to a frozen range.
    """
    job = db.get(IngestionJob, job_id)
    if not job or job.job_type != JOB_TYPE:
        return {"status": "missing"}
    checkpoint = deepcopy(job.checkpoint)
    if (job.status != "queued" or generation != checkpoint.get("dispatch_generation")
            or generation <= checkpoint.get("claimed_generation", -1)):
        return {"status": "duplicate_delivery"}
    if not settings.historical_ingestion_enabled or not settings.gee_project:
        return {"status": "disabled"}
    if aware(checkpoint["next_step_at"]) > utcnow():
        return {"status": "not_due"}
    with distributed_lock("queued-job:%s" % job_id) as acquired:
        if not acquired:
            return {"status": "locked"}
        db.refresh(job)
        checkpoint = deepcopy(job.checkpoint)
        if (job.status != "queued" or generation != checkpoint.get("dispatch_generation")
                or generation <= checkpoint.get("claimed_generation", -1)):
            return {"status": "duplicate_delivery"}
        with distributed_lock("monitor:%s" % job.source_id) as source_acquired:
            if not source_acquired:
                checkpoint["next_step_at"] = (utcnow() + timedelta(seconds=60)).isoformat()
                checkpoint.pop("dispatch_until", None)
                _store(db, job_id, checkpoint, stage="source_busy")
                return {"status": "locked"}
            _recover_interrupted_source(db, job.source_id)
            config = BackfillRequest.model_validate(checkpoint["configuration"])
            connector = connector or CONNECTOR_TYPES[config.source]()
            if not isinstance(connector, EarthEngineConnector):
                raise ValueError("Historical ingestion requires an Earth Engine connector")
            checkpoint["running_until"] = (utcnow() + timedelta(seconds=MONITOR_LOCK_LEASE_SECONDS)).isoformat()
            checkpoint["claimed_generation"] = generation
            db.execute(update(IngestionJob).where(IngestionJob.id == job_id, IngestionJob.status == "queued").values(
                status="running", stage="historical_step", attempts=IngestionJob.attempts + 1,
                started_at=job.started_at or utcnow(), checkpoint=checkpoint))
            db.commit()
            db.refresh(job)
            if job.status != "running":
                return {"status": "cancelled"}
            source = db.get(DataSource, job.source_id)
            try:
                if source is None or not source.enabled:
                    raise ValueError("Historical data source was disabled")
                if not checkpoint["pending"] and not checkpoint["catalogue_done"]:
                    if checkpoint["catalogue_requests"] >= config.max_catalogue_requests:
                        _store(db, job_id, checkpoint, "completed", "budget_exhausted")
                        return {"status": "budget_exhausted"}
                    checkpoint["catalogue_requests"] += 1
                    _store(db, job_id, checkpoint, "running", "catalogue_query")
                    db.refresh(job)
                    if job.status != "running":
                        return {"status": "cancelled"}
                    page = connector.discover_historical_page(config.start_date, config.end_date,
                        checkpoint["cursor"], config.page_size, config.window_days, config.months)
                    checkpoint.update(cursor=page.cursor, pending=[_serialize(asset) for asset in page.assets],
                                      catalogue_done=page.done, scanned=checkpoint["scanned"] + page.scanned_count)
                    _store(db, job_id, checkpoint, "running", "catalogue_staged")
                # Drop only already-final assets. Processing errors are retryable.
                while checkpoint["pending"]:
                    asset = _asset(checkpoint["pending"][0])
                    existing = db.scalar(select(DatasetVersion).where(DatasetVersion.source_id == source.id,
                                              DatasetVersion.source_asset_id == asset.source_asset_id))
                    if not existing or (existing.status == "failed" and existing.quality_status == "processing_error"):
                        break
                    checkpoint["pending"].pop(0)
                    checkpoint["skipped"] += 1
                if checkpoint["pending"]:
                    if checkpoint["processing_attempts"] >= config.max_assets:
                        _store(db, job_id, checkpoint, "completed", "budget_exhausted")
                        return {"status": "budget_exhausted"}
                    checkpoint["processing_attempts"] += 1
                    _store(db, job_id, checkpoint, "running", "processing")
                    db.refresh(job)
                    if job.status != "running":
                        return {"status": "cancelled"}
                    child = IngestionJob(source_id=source.id, job_type="historical_asset", status="running",
                        stage="staging", attempts=1, started_at=utcnow(), requested_by=job.requested_by,
                        checkpoint={"parent_job_id": job_id})
                    db.add(child)
                    db.commit()
                    # Full official area coverage: an asset ID is never claimed for an arbitrary pilot subset.
                    ingest_asset(db, source, connector, asset, child)
                    checkpoint["pending"].pop(0)
                checkpoint["consecutive_errors"] = 0
                checkpoint.pop("dispatch_until", None)
                checkpoint["next_step_at"] = (utcnow() + timedelta(seconds=config.step_delay_seconds)).isoformat()
                complete = checkpoint["catalogue_done"] and not checkpoint["pending"]
                exhausted = checkpoint["processing_attempts"] >= config.max_assets
                stage = "completed" if complete else "budget_exhausted" if exhausted else "waiting"
                _store(db, job_id, checkpoint, "completed" if complete or exhausted else "queued", stage)
                return {"status": stage}
            except Exception as exc:
                db.rollback()
                checkpoint["consecutive_errors"] += 1
                retryable = isinstance(exc, (ConnectorError, ConnectionError, TimeoutError, SoftTimeLimitExceeded))
                exhausted = (checkpoint["processing_attempts"] >= config.max_assets or
                             (not checkpoint["pending"] and checkpoint["catalogue_requests"] >= config.max_catalogue_requests))
                retry = retryable and checkpoint["consecutive_errors"] <= 5 and not exhausted
                delay = max(config.step_delay_seconds, exc.retry_after_seconds if isinstance(exc, ProviderRateLimitError)
                            else min(900, 60 * 2 ** checkpoint["consecutive_errors"]))
                checkpoint.pop("dispatch_until", None)
                checkpoint["next_step_at"] = (utcnow() + timedelta(seconds=delay)).isoformat()
                _store(db, job_id, checkpoint, "queued" if retry else "failed",
                       "retry_scheduled" if retry else "budget_exhausted" if exhausted else "failed", str(exc))
                return {"status": "retry_scheduled" if retry else "failed"}


def dispatch_backfills(db: Session, send: Callable[[str, int], None]):
    if not settings.historical_ingestion_enabled or not settings.gee_project:
        return {"status": "disabled", "dispatched": 0}
    dispatched = 0
    with distributed_lock("historical-dispatch", timeout_seconds=60) as acquired:
        if not acquired:
            return {"status": "locked", "dispatched": 0}
        # At most one active job per each of three providers; bound the control loop anyway.
        jobs = db.scalars(select(IngestionJob).where(IngestionJob.job_type == JOB_TYPE,
            IngestionJob.status.in_(ACTIVE)).order_by(IngestionJob.created_at).limit(5)).all()
        for job in jobs:
            checkpoint = deepcopy(job.checkpoint)
            now = utcnow()
            if job.status in ("running", "cancel_requested"):
                if aware(checkpoint.get("running_until", now.isoformat())) > now:
                    continue
                if job.status == "cancel_requested":
                    _store(db, job.id, checkpoint)
                    continue
                _store(db, job.id, checkpoint, stage="worker_recovery")
            if aware(checkpoint["next_step_at"]) > now or aware(checkpoint.get("dispatch_until", now.isoformat())) > now:
                continue
            checkpoint["dispatch_generation"] += 1
            checkpoint["dispatch_until"] = (now + timedelta(seconds=MONITOR_LOCK_LEASE_SECONDS)).isoformat()
            _store(db, job.id, checkpoint, stage="dispatched")
            try:
                send(job.id, checkpoint["dispatch_generation"])
                dispatched += 1
            except Exception as exc:
                checkpoint.pop("dispatch_until", None)
                checkpoint["next_step_at"] = (now + timedelta(seconds=60)).isoformat()
                _store(db, job.id, checkpoint, stage="dispatch_retry", error=str(exc))
    return {"status": "completed", "dispatched": dispatched}
