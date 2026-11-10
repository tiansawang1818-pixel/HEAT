"""Celery settings shared by workers and the beat scheduler.

Beat only publishes task names on a timetable, so it loads this module
instead of app.worker and never imports Earth Engine, the pipeline or the
optimizer (about 100 MB less resident memory on the 768 MiB runtime).
"""
from celery import Celery

from app.core.config import settings
from app.core.redis_tls import celery_redis_tls_settings
from app.services.locks import MONITOR_HARD_TIME_LIMIT_SECONDS, MONITOR_SOFT_TIME_LIMIT_SECONDS


def celery_settings() -> dict:
    return dict(
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


def make_celery() -> Celery:
    app = Celery("heatshift", broker=settings.redis_url, backend=settings.redis_url)
    app.conf.update(**celery_settings())
    return app


beat_app = make_celery()
