import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    AreaIndicator,
    DataSource,
    DatasetVersion,
    IngestionJob,
    OptimizationRun,
    PipelineEvent,
    ProcessingStatus,
    SourceCheck,
    ValidationReport,
)
from app.services.connectors.base import ConnectorError, SourceAsset, SourceConnector
from app.services.locks import distributed_lock


logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _latest_version(db: Session, source_id: str) -> Optional[DatasetVersion]:
    return db.scalar(
        select(DatasetVersion)
        .where(DatasetVersion.source_id == source_id)
        .order_by(DatasetVersion.observed_at.desc())
        .limit(1)
    )


def _publish_fixture_indicators(db: Session, version: DatasetVersion, payload: Dict[str, Any]) -> None:
    entries = payload.get("area_indicators", []) if isinstance(payload, dict) else []
    for entry in entries:
        if not entry.get("area_id") or not entry.get("indicator_key"):
            continue
        db.add(
            AreaIndicator(
                area_id=entry["area_id"],
                dataset_version_id=version.id,
                indicator_key=entry["indicator_key"],
                value=entry.get("value"),
                unit=entry.get("unit"),
                observed_at=version.observed_at or utcnow(),
                quality_status="synthetic_demo",
                metadata_json={"fixture": True, "scientific_use": False},
            )
        )


def ingest_asset(db: Session, source: DataSource, connector: SourceConnector, asset: SourceAsset, job: IngestionJob) -> DatasetVersion:
    existing = db.scalar(
        select(DatasetVersion).where(
            DatasetVersion.source_id == source.id, DatasetVersion.source_asset_id == asset.source_asset_id
        )
    )
    if existing:
        job.stage = "skipped"
        job.progress = 1.0
        return existing

    version = DatasetVersion(
        source_id=source.id,
        source_asset_id=asset.source_asset_id,
        source_version=asset.source_version,
        checksum=asset.checksum,
        observed_at=asset.observed_at,
        geographic_extent=asset.geographic_extent,
        metadata_json={
            **asset.metadata,
            **({"provider_payload": asset.payload} if asset.payload else {}),
        },
        status=ProcessingStatus.staged.value,
    )
    db.add(version)
    db.flush()
    job.dataset_version_id = version.id
    job.stage = "validation"
    job.progress = 0.3
    version.status = ProcessingStatus.validating.value
    report = connector.validate(asset)
    db.add(
        ValidationReport(
            dataset_version_id=version.id,
            passed=bool(report["passed"]),
            checks=report.get("metrics", {}),
            warnings=report.get("warnings", []),
            errors=report.get("errors", []),
        )
    )
    if not report["passed"]:
        version.status = ProcessingStatus.failed.value
        version.quality_status = "rejected"
        job.status = "failed"
        job.stage = "validation_failed"
        job.error_message = "; ".join(report.get("errors", []))
        job.finished_at = utcnow()
        db.commit()
        return version

    version.status = ProcessingStatus.processing.value
    version.quality_status = "valid"
    version.ingested_at = utcnow()
    version.validated_at = utcnow()
    job.stage = "publishing"
    job.progress = 0.8
    current_layer = db.scalar(select(func.max(DatasetVersion.layer_version)).where(DatasetVersion.source_id == source.id)) or 0
    version.layer_version = current_layer + 1
    version.status = ProcessingStatus.published.value
    version.published_at = utcnow()
    if connector.layer_key == "fixture":
        _publish_fixture_indicators(db, version, asset.payload)
    for optimization in db.scalars(select(OptimizationRun).where(OptimizationRun.is_outdated.is_(False))).all():
        recorded = optimization.input_versions.get(source.dataset_id) if optimization.input_versions else None
        if recorded is None or recorded.get("version_id") != version.id:
            optimization.is_outdated = True
    db.add(
        PipelineEvent(
            event_type="layer.published",
            source_id=source.id,
            dataset_version_id=version.id,
            layer_key=connector.layer_key,
            layer_version=version.layer_version,
            payload={
                "source_name": source.name,
                "observed_at": version.observed_at.isoformat() if version.observed_at else None,
                "affected_layers": [connector.layer_key],
                "cache_tags": ["layer:%s" % connector.layer_key],
            },
        )
    )
    job.status = "completed"
    job.stage = "published"
    job.progress = 1.0
    job.finished_at = utcnow()
    db.commit()
    return version


def run_monitor(db: Session, connector: SourceConnector, requested_by: str = "scheduler") -> Dict[str, Any]:
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == connector.dataset_id))
    if source is None:
        raise ValueError("Unknown data source: %s" % connector.dataset_id)
    with distributed_lock("monitor:%s" % source.id) as acquired:
        if not acquired:
            return {"status": "locked", "source": source.name, "discovered": 0, "processed": 0}
        check = SourceCheck(source_id=source.id)
        db.add(check)
        db.commit()
        latest = _latest_version(db, source.id)
        try:
            assets = connector.discover(latest.source_asset_id if latest else None)
            check.discovered_count = len(assets)
            processed: List[str] = []
            jobs: List[str] = []
            for asset in assets:
                duplicate = db.scalar(
                    select(DatasetVersion.id).where(
                        DatasetVersion.source_id == source.id, DatasetVersion.source_asset_id == asset.source_asset_id
                    )
                )
                if duplicate:
                    continue
                job = IngestionJob(
                    source_id=source.id,
                    job_type="ingest",
                    status="running",
                    stage="staging",
                    progress=0.1,
                    attempts=1,
                    requested_by=requested_by,
                    started_at=utcnow(),
                )
                db.add(job)
                db.commit()
                result = ingest_asset(db, source, connector, asset, job)
                processed.append(result.id)
                jobs.append(job.id)
            check.queued_count = len(jobs)
            check.status = "success"
            check.completed_at = utcnow()
            db.commit()
            return {"status": "success", "source": source.name, "discovered": len(assets), "processed": len(processed), "job_ids": jobs}
        except Exception as exc:
            db.rollback()
            check = db.get(SourceCheck, check.id)
            check.status = "failed"
            check.error_message = str(exc)
            check.completed_at = utcnow()
            db.commit()
            logger.exception("Source monitor failed for %s", source.name)
            raise
