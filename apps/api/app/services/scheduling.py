"""Durable, cadence-aware dispatch. This module never calls a data provider."""
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.entities import DataSource, GeographicArea, IngestionJob, SourceCheck
from app.services.locks import MONITOR_LOCK_LEASE_SECONDS, distributed_lock
from app.services.source_registry import SOURCE_KEYS


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def dispatch_due_sources(db: Session, enqueue: Callable, *, now: Optional[datetime] = None):
    now = now or datetime.now(timezone.utc)
    dispatched, deferred = [], []
    with distributed_lock("scheduler:source-dispatch", timeout_seconds=120) as acquired:
        if not acquired:
            return {"status": "locked", "dispatched": [], "deferred": []}
        has_boundaries = bool(db.scalar(select(func.count()).select_from(GeographicArea).where(
            GeographicArea.quality_status == "verified_official_boundary"
        )))
        sources = db.scalars(select(DataSource).where(DataSource.enabled.is_(True)).order_by(DataSource.dataset_id)).all()
        for source in sources:
            source_key = SOURCE_KEYS.get(source.dataset_id)
            if source_key is None or source.check_interval_seconds <= 0:
                continue
            if source_key not in {"bma-boundaries", "open-meteo"} and not has_boundaries:
                deferred.append({"source": source_key, "reason": "waiting_for_validated_boundaries"})
                continue
            parent = db.scalar(select(IngestionJob).where(
                IngestionJob.source_id == source.id,
                IngestionJob.job_type.in_(["scheduled_refresh", "manual_refresh"]),
                IngestionJob.status.in_(["queued", "running"]),
            ).order_by(desc(IngestionJob.created_at)).limit(1))
            if parent:
                checkpoint = parent.checkpoint or {}
                reference = parent.started_at or parent.created_at
                if parent.stage == "dispatch_pending":
                    lease = 120
                else:
                    lease = MONITOR_LOCK_LEASE_SECONDS + 60
                for field in ("dispatched_at", "next_retry_at"):
                    if checkpoint.get(field):
                        reference = max(_aware(reference), _aware(datetime.fromisoformat(checkpoint[field])))
                if (now - _aware(reference)).total_seconds() < lease:
                    continue
                # Replay the same durable job ID after its execution lease.
                # The worker's job/source locks and asset IDs make redelivery safe.
                job = parent
            else:
                check = db.scalar(select(SourceCheck).where(SourceCheck.source_id == source.id).order_by(desc(SourceCheck.checked_at)).limit(1))
                if check and now < _aware(check.checked_at) + timedelta(seconds=source.check_interval_seconds):
                    continue
                last_failed_dispatch = db.scalar(select(IngestionJob).where(
                    IngestionJob.source_id == source.id, IngestionJob.stage == "dispatch_failed",
                ).order_by(desc(IngestionJob.created_at)).limit(1))
                if last_failed_dispatch and now < _aware(last_failed_dispatch.finished_at) + timedelta(seconds=60):
                    continue
                job = IngestionJob(source_id=source.id, job_type="scheduled_refresh", requested_by="scheduler:due-source-check", created_at=now)
                db.add(job)
            job.status, job.stage = "queued", "dispatch_pending"
            job.error_message, job.finished_at = None, None
            job.checkpoint = {**(job.checkpoint or {}), "dispatched_at": now.isoformat()}
            db.commit()
            try:
                task = enqueue(job.id, source_key)
                # Do not overwrite a worker that has already claimed/completed it.
                db.refresh(job)
                if job.stage == "dispatch_pending":
                    job.stage = "queued"
                    job.checkpoint = {**job.checkpoint, "celery_task_id": str(task.id)}
                    db.commit()
                dispatched.append({"source": source_key, "job_id": job.id})
            except Exception:
                db.rollback()
                job = db.get(IngestionJob, job.id)
                if job.stage == "dispatch_pending":
                    job.status, job.stage = "failed", "dispatch_failed"
                    job.error_message = "Durable queue dispatch failed; the scheduler will retry after a cooldown"
                    job.finished_at = now
                    db.commit()
                deferred.append({"source": source_key, "reason": "queue_unavailable"})
    return {"status": "completed", "dispatched": dispatched, "deferred": deferred}
