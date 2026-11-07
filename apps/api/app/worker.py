import random
import time
import redis
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from celery import Celery
from billiard.exceptions import SoftTimeLimitExceeded

from app.core.config import settings
from app.core.redis_tls import celery_redis_tls_settings
from app.db.session import SessionLocal
from app.models.entities import IngestionJob
from app.services.bootstrap import ensure_sources
from app.services.connectors import FixtureConnector
from app.services.connectors.base import ConnectorError, ProviderRateLimitError
from app.services.pipeline import run_monitor
from app.services.locks import MONITOR_HARD_TIME_LIMIT_SECONDS, MONITOR_SOFT_TIME_LIMIT_SECONDS
from app.services.locks import LockBackendUnavailable, distributed_lock
from app.services.source_registry import CONNECTOR_TYPES
from app.services.scheduling import dispatch_due_sources
from app.services.forecast_workflow import update_scheduled_forecasts
from app.services.historical import dispatch_backfills, execute_backfill_step


celery_app = Celery("heatshift", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    **celery_redis_tls_settings(settings.redis_url),
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=MONITOR_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=MONITOR_HARD_TIME_LIMIT_SECONDS,
    broker_connection_retry_on_startup=True,
    task_routes={"app.worker.dispatch_ingestion": {"queue": "control"}},
    beat_schedule={
        "dispatch-due-source-checks": {"task": "app.worker.dispatch_ingestion", "schedule": settings.scheduler_tick_seconds},
        "update-temperature-scenarios": {"task": "app.worker.update_forecast_models", "schedule": 3600},
    },
)


def connector_for(source: str, fixture_path: Optional[str] = None):
    if source == "fixture":
        if not settings.allow_fixture_ingestion or not fixture_path:
            raise ValueError("Fixture ingestion is disabled or missing path")
        return FixtureConnector(Path(fixture_path))
    if source not in CONNECTOR_TYPES:
        raise ValueError("Unsupported source: %s" % source)
    return CONNECTOR_TYPES[source]()


@celery_app.task(name="app.worker.dispatch_ingestion", soft_time_limit=45, time_limit=60)
def dispatch_ingestion():
    with SessionLocal() as db:
        ensure_sources(db)
        result = dispatch_due_sources(db, lambda job_id, source: run_manual_ingestion.delay(job_id, source))
        result["historical"] = dispatch_backfills(db, lambda job_id, generation: run_historical_step.delay(job_id, generation))
        if result["status"] == "completed":
            with redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1) as client:
                client.set("heatshift:dispatcher:last_tick", str(time.time()), ex=300)
        return result


@celery_app.task(name="app.worker.run_historical_step")
def run_historical_step(job_id: str, generation: int):
    with SessionLocal() as db:
        return execute_backfill_step(db, job_id, generation)


@celery_app.task(name="app.worker.update_forecast_models", soft_time_limit=540, time_limit=600)
def update_forecast_models():
    with SessionLocal() as db:
        return update_scheduled_forecasts(db)


@celery_app.task(
    bind=True,
    autoretry_for=(ConnectorError, ConnectionError, TimeoutError, SoftTimeLimitExceeded),
    retry_backoff=True,
    retry_backoff_max=900,
    retry_jitter=True,
    max_retries=5,
    name="app.worker.run_ingestion",
)
def run_ingestion(self, source: str, fixture_path: Optional[str] = None):
    db = SessionLocal()
    try:
        ensure_sources(db)
        try:
            return run_monitor(db, connector_for(source, fixture_path), requested_by="celery:%s" % self.request.id)
        except ProviderRateLimitError as exc:
            raise self.retry(exc=exc, countdown=exc.retry_after_seconds, max_retries=5)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=5, autoretry_for=(LockBackendUnavailable,), retry_backoff=True, retry_jitter=True, name="app.worker.run_manual_ingestion")
def run_manual_ingestion(self, job_id: str, source: str, fixture_path: Optional[str] = None):
    with distributed_lock("queued-job:%s" % job_id) as acquired:
        if not acquired:
            return {"status": "duplicate_delivery", "job_id": job_id}
        return _execute_queued_ingestion(self, job_id, source, fixture_path)


def _execute_queued_ingestion(self, job_id: str, source: str, fixture_path: Optional[str] = None):
    db = SessionLocal()
    try:
        job = db.get(IngestionJob, job_id)
        if job is None:
            raise ValueError("Manual ingestion job does not exist")
        if job.status == "completed":
            return job.checkpoint
        job.status, job.stage, job.started_at, job.progress = "running", "source_check", datetime.now(timezone.utc), 0.1
        job.attempts = self.request.retries + 1
        job.error_message, job.finished_at = None, None
        job.checkpoint = {"celery_task_id": self.request.id}
        db.commit()
        result = run_monitor(db, connector_for(source, fixture_path), requested_by=job.requested_by)
        if result.get("status") == "locked":
            raise ConnectorError("Another worker is processing this source; retry has been scheduled")
        job = db.get(IngestionJob, job_id)
        job.status, job.stage, job.finished_at, job.progress = "completed", "completed", datetime.now(timezone.utc), 1.0
        job.checkpoint = {**result, "celery_task_id": self.request.id}
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        job = db.get(IngestionJob, job_id)
        if job:
            retryable = isinstance(exc, (ConnectorError, ConnectionError, TimeoutError, SoftTimeLimitExceeded))
            if retryable and self.request.retries < self.max_retries:
                countdown = exc.retry_after_seconds if isinstance(exc, ProviderRateLimitError) else min(900, 30 * 2 ** self.request.retries) + random.randint(0, 15)
                job.status, job.stage, job.finished_at, job.error_message = "queued", "retry_scheduled", None, str(exc)
                job.checkpoint = {
                    **(job.checkpoint or {}),
                    "next_retry_at": (datetime.now(timezone.utc) + timedelta(seconds=countdown)).isoformat(),
                    "retry_after_seconds": countdown,
                }
                db.commit()
                raise self.retry(exc=exc, countdown=countdown)
            job.status, job.stage, job.finished_at, job.error_message = "failed", "failed", datetime.now(timezone.utc), str(exc)
            db.commit()
        raise
    finally:
        db.close()
