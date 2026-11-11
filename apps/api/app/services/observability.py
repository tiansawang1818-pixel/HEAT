import threading
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import DefaultDict, Dict, Tuple

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session
import redis

from app.core.config import settings

from app.models.entities import (
    DataSource,
    DatasetVersion,
    IngestionJob,
    SourceCheck,
    ValidationReport,
)


MetricKey = Tuple[str, str, str]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**values: object) -> str:
    return ",".join('%s="%s"' % (key, _label(value)) for key, value in values.items())


class RequestTelemetry:
    """Small process-local HTTP counters with bounded route labels.

    Database and pipeline metrics are calculated from durable state below. HTTP
    counters intentionally use FastAPI route templates, never raw paths, so area
    IDs and other user-controlled values cannot create unbounded cardinality.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: DefaultDict[MetricKey, int] = defaultdict(int)
        self._duration_seconds: DefaultDict[MetricKey, float] = defaultdict(float)

    def observe(self, method: str, route: str, status_code: int, duration_seconds: float) -> None:
        key = (method.upper(), route, str(status_code))
        with self._lock:
            self._counts[key] += 1
            self._duration_seconds[key] += duration_seconds

    def snapshot(self) -> Tuple[Dict[MetricKey, int], Dict[MetricKey, float]]:
        with self._lock:
            return dict(self._counts), dict(self._duration_seconds)


request_telemetry = RequestTelemetry()


def prometheus_metrics(db: Session) -> str:
    now = datetime.now(timezone.utc)
    lines = [
        "# HELP heatshift_info HEAT//SHIFT service information.",
        "# TYPE heatshift_info gauge",
        'heatshift_info{service="api"} 1',
        "# HELP heatshift_http_requests_total HTTP requests observed by this API process.",
        "# TYPE heatshift_http_requests_total counter",
    ]
    request_counts, duration_sums = request_telemetry.snapshot()
    heartbeat = None
    try:
        with redis.Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3) as client:
            raw = client.get("heatshift:dispatcher:last_tick")
            heartbeat = float(raw) if raw else None
            if heartbeat is not None and not math.isfinite(heartbeat):
                heartbeat = None
    except (redis.RedisError, ValueError, TypeError):
        pass  # A missing metric signals the outage; never serialize connection details.
    lines.extend([
        "# HELP heatshift_dispatcher_heartbeat_present Whether a recent control worker heartbeat exists.",
        "# TYPE heatshift_dispatcher_heartbeat_present gauge",
        "heatshift_dispatcher_heartbeat_present %d" % (heartbeat is not None),
        "# HELP heatshift_dispatcher_heartbeat_age_seconds Age of the last dispatcher heartbeat.",
        "# TYPE heatshift_dispatcher_heartbeat_age_seconds gauge",
        "heatshift_dispatcher_heartbeat_age_seconds %.3f" % (max(0, now.timestamp() - heartbeat) if heartbeat is not None else 0),
    ])
    for (method, route, status), count in sorted(request_counts.items()):
        labels = _labels(method=method, route=route, status=status)
        lines.append("heatshift_http_requests_total{%s} %d" % (labels, count))
    lines.extend(
        [
            "# HELP heatshift_http_request_duration_seconds_sum Total HTTP request duration by route.",
            "# TYPE heatshift_http_request_duration_seconds_sum counter",
        ]
    )
    for (method, route, status), duration in sorted(duration_sums.items()):
        labels = _labels(method=method, route=route, status=status)
        lines.append("heatshift_http_request_duration_seconds_sum{%s} %.6f" % (labels, duration))

    lines.extend(
        [
            "# HELP heatshift_dataset_versions_total Dataset versions by processing status.",
            "# TYPE heatshift_dataset_versions_total gauge",
        ]
    )
    for status, count in db.execute(
        select(DatasetVersion.status, func.count()).group_by(DatasetVersion.status)
    ).all():
        lines.append('heatshift_dataset_versions_total{status="%s"} %d' % (_label(status), count))

    lines.extend(
        [
            "# HELP heatshift_ingestion_jobs_total Ingestion jobs by current status.",
            "# TYPE heatshift_ingestion_jobs_total gauge",
        ]
    )
    for status, count in db.execute(
        select(IngestionJob.status, func.count()).group_by(IngestionJob.status)
    ).all():
        lines.append('heatshift_ingestion_jobs_total{status="%s"} %d' % (_label(status), count))

    day_ago = now - timedelta(days=1)
    failed_jobs_24h = db.scalar(
        select(func.count()).select_from(IngestionJob).where(
            IngestionJob.status == "failed",
            IngestionJob.created_at >= day_ago,
        )
    ) or 0
    failed_validations_24h = db.scalar(
        select(func.count()).select_from(ValidationReport).where(
            ValidationReport.passed.is_(False),
            ValidationReport.created_at >= day_ago,
        )
    ) or 0
    oldest_active = db.scalar(
        select(IngestionJob.created_at)
        .where(IngestionJob.status.in_(["queued", "running"]))
        .order_by(IngestionJob.created_at)
        .limit(1)
    )
    active_age = max(0.0, (now - _aware(oldest_active)).total_seconds()) if oldest_active else 0.0
    lines.extend(
        [
            "# HELP heatshift_ingestion_failed_jobs_24h Failed ingestion jobs created in the last 24 hours.",
            "# TYPE heatshift_ingestion_failed_jobs_24h gauge",
            "heatshift_ingestion_failed_jobs_24h %d" % failed_jobs_24h,
            "# HELP heatshift_validation_failures_24h Failed validation reports created in the last 24 hours.",
            "# TYPE heatshift_validation_failures_24h gauge",
            "heatshift_validation_failures_24h %d" % failed_validations_24h,
            "# HELP heatshift_oldest_active_ingestion_job_age_seconds Age of the oldest queued or running ingestion job.",
            "# TYPE heatshift_oldest_active_ingestion_job_age_seconds gauge",
            "heatshift_oldest_active_ingestion_job_age_seconds %.3f" % active_age,
            "# HELP heatshift_data_source_last_check_age_seconds Seconds since the latest system check.",
            "# TYPE heatshift_data_source_last_check_age_seconds gauge",
            "# HELP heatshift_data_source_last_observation_age_seconds Seconds since the latest published source observation.",
            "# TYPE heatshift_data_source_last_observation_age_seconds gauge",
            "# HELP heatshift_data_source_check_interval_seconds Configured source monitoring interval.",
            "# TYPE heatshift_data_source_check_interval_seconds gauge",
            "# HELP heatshift_data_source_available Whether a source has a published validated version.",
            "# TYPE heatshift_data_source_available gauge",
            "# HELP heatshift_data_source_stale Whether the latest published observation exceeds its configured threshold.",
            "# TYPE heatshift_data_source_stale gauge",
        ]
    )
    sources = db.scalars(
        select(DataSource).where(DataSource.enabled.is_(True), DataSource.source_type != "test_fixture")
    ).all()
    for source in sources:
        labels = _labels(dataset_id=source.dataset_id, provider=source.provider)
        lines.append("heatshift_data_source_check_interval_seconds{%s} %d" % (labels, source.check_interval_seconds))
        latest_check = db.scalar(
            select(SourceCheck.checked_at)
            .where(SourceCheck.source_id == source.id)
            .order_by(desc(SourceCheck.checked_at))
            .limit(1)
        )
        lines.append(
            "heatshift_data_source_last_check_age_seconds{%s} %.3f"
            % (labels, max(0.0, (now - _aware(latest_check or source.created_at)).total_seconds()))
        )
        latest_observation = db.scalar(
            select(DatasetVersion.observed_at)
            .where(
                DatasetVersion.source_id == source.id,
                DatasetVersion.status == "published",
                DatasetVersion.observed_at.is_not(None),
            )
            .order_by(desc(DatasetVersion.observed_at))
            .limit(1)
        )
        available = latest_observation is not None
        observation_age = max(0.0, (now - _aware(latest_observation)).total_seconds()) if available else 0.0
        lines.append("heatshift_data_source_available{%s} %d" % (labels, 1 if available else 0))
        lines.append(
            "heatshift_data_source_stale{%s} %d"
            % (labels, 1 if available and observation_age > source.stale_after_seconds else 0)
        )
        if available:
            lines.append("heatshift_data_source_last_observation_age_seconds{%s} %.3f" % (labels, observation_age))
    return "\n".join(lines) + "\n"
