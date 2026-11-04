from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from celery import Celery

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import IngestionJob
from app.services.bootstrap import ensure_sources
from app.services.connectors import (
    DynamicWorldConnector,
    FixtureConnector,
    LandsatConnector,
    OpenMeteoConnector,
    OpenStreetMapConnector,
    SentinelConnector,
    WorldPopConnector,
)
from app.services.connectors.base import ConnectorError, ProviderRateLimitError
from app.services.pipeline import run_monitor


celery_app = Celery("heatshift", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=50 * 60,
    task_time_limit=60 * 60,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "monitor-landsat-daily": {"task": "app.worker.run_ingestion", "schedule": 86400, "args": ["landsat"]},
        "monitor-sentinel-six-hourly": {"task": "app.worker.run_ingestion", "schedule": 21600, "args": ["sentinel"]},
        "monitor-dynamic-world-six-hourly": {"task": "app.worker.run_ingestion", "schedule": 21600, "args": ["dynamic-world"]},
        "monitor-weather-hourly": {"task": "app.worker.run_ingestion", "schedule": 3600, "args": ["open-meteo"]},
        "monitor-worldpop-monthly": {"task": "app.worker.run_ingestion", "schedule": 30 * 86400, "args": ["worldpop"]},
        "monitor-osm-weekly": {"task": "app.worker.run_ingestion", "schedule": 7 * 86400, "args": ["osm"]},
    },
)


def connector_for(source: str, fixture_path: Optional[str] = None):
    connectors = {
        "landsat": LandsatConnector,
        "sentinel": SentinelConnector,
        "dynamic-world": DynamicWorldConnector,
        "open-meteo": OpenMeteoConnector,
        "worldpop": WorldPopConnector,
        "osm": OpenStreetMapConnector,
    }
    if source == "fixture":
        if not settings.allow_fixture_ingestion or not fixture_path:
            raise ValueError("Fixture ingestion is disabled or missing path")
        return FixtureConnector(Path(fixture_path))
    if source not in connectors:
        raise ValueError("Unsupported source: %s" % source)
    return connectors[source]()


@celery_app.task(
    bind=True,
    autoretry_for=(ConnectorError, ConnectionError, TimeoutError),
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


@celery_app.task(bind=True, name="app.worker.run_manual_ingestion")
def run_manual_ingestion(self, job_id: str, source: str, fixture_path: Optional[str] = None):
    db = SessionLocal()
    try:
        job = db.get(IngestionJob, job_id)
        if job is None:
            raise ValueError("Manual ingestion job does not exist")
        job.status, job.stage, job.started_at, job.progress = "running", "source_check", datetime.now(timezone.utc), 0.1
        job.checkpoint = {"celery_task_id": self.request.id}
        db.commit()
        result = run_monitor(db, connector_for(source, fixture_path), requested_by="admin:%s" % self.request.id)
        job = db.get(IngestionJob, job_id)
        job.status, job.stage, job.finished_at, job.progress = "completed", "completed", datetime.now(timezone.utc), 1.0
        job.checkpoint = {**result, "celery_task_id": self.request.id}
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        job = db.get(IngestionJob, job_id)
        if job:
            job.status, job.stage, job.finished_at, job.error_message = "failed", "failed", datetime.now(timezone.utc), str(exc)
            db.commit()
        raise
    finally:
        db.close()
