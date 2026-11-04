import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    AreaIndicator,
    DataSource,
    DatasetVersion,
    GeographicArea,
    IngestionJob,
    OptimizationRun,
    PipelineEvent,
    PopulationEstimate,
    ProcessingStatus,
    SourceCheck,
    TemperatureObservation,
    ValidationReport,
    VegetationObservation,
)
from app.services.analytics import recalculate_priority_scores
from app.services.connectors.base import AreaProcessingRecord, SourceAsset, SourceConnector
from app.services.geometry import to_geojson
from app.services.locks import distributed_lock


logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _latest_version(db: Session, source_id: str) -> Optional[DatasetVersion]:
    return db.scalar(
        select(DatasetVersion)
        .where(DatasetVersion.source_id == source_id, DatasetVersion.status == ProcessingStatus.published.value)
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


def _area_inputs(db: Session) -> List[Dict[str, Any]]:
    return [
        {"id": area.id, "geometry": to_geojson(area.geometry)}
        for area in db.scalars(select(GeographicArea)).all()
    ]


def _persist_area_records(
    db: Session,
    connector: SourceConnector,
    version: DatasetVersion,
    records: Sequence[AreaProcessingRecord],
) -> None:
    observed_at = version.observed_at or utcnow()
    for record in records:
        metadata = {
            **record.metadata,
            "coverage_fraction": record.coverage_fraction,
            "source_asset_id": version.source_asset_id,
        }
        for key, value in record.indicators.items():
            if value is None:
                continue
            db.add(
                AreaIndicator(
                    area_id=record.area_id,
                    dataset_version_id=version.id,
                    indicator_key=key,
                    value=float(value),
                    unit=record.units.get(key),
                    observed_at=observed_at,
                    quality_status=record.quality_status,
                    metadata_json=metadata,
                )
            )
        if connector.layer_key == "temperature":
            db.add(
                TemperatureObservation(
                    area_id=record.area_id,
                    dataset_version_id=version.id,
                    observed_at=observed_at,
                    lst_c_mean=record.indicators.get("lst_c_mean"),
                    lst_c_p90=record.indicators.get("lst_c_p90"),
                    coverage_fraction=float(record.coverage_fraction or 0),
                    uncertainty_c=record.metadata.get("uncertainty_c"),
                )
            )
        elif connector.layer_key in {"ndvi", "landcover"}:
            tree_pct = record.indicators.get("tree_cover_fraction")
            built_pct = record.indicators.get("built_fraction")
            db.add(
                VegetationObservation(
                    area_id=record.area_id,
                    dataset_version_id=version.id,
                    observed_at=observed_at,
                    ndvi_mean=record.indicators.get("ndvi"),
                    tree_cover_fraction=float(tree_pct) / 100 if tree_pct is not None else None,
                    built_fraction=float(built_pct) / 100 if built_pct is not None else None,
                    confidence=record.metadata.get("confidence"),
                )
            )
        elif connector.layer_key == "population":
            db.add(
                PopulationEstimate(
                    area_id=record.area_id,
                    dataset_version_id=version.id,
                    reference_year=int(record.metadata["reference_year"]),
                    population=float(record.indicators["population_estimate"]),
                    density_per_sq_km=float(record.indicators["population_density"]),
                    uncertainty_fraction=record.metadata.get("uncertainty_fraction"),
                )
            )


def _validation_report(
    db: Session,
    version: DatasetVersion,
    asset_report: Dict[str, Any],
    processing_report: Optional[Dict[str, Any]] = None,
) -> None:
    processing_report = processing_report or {"passed": True, "metrics": {}, "warnings": [], "errors": []}
    db.add(
        ValidationReport(
            dataset_version_id=version.id,
            passed=bool(asset_report["passed"] and processing_report["passed"]),
            checks={
                "asset": asset_report.get("metrics", {}),
                "processing": processing_report.get("metrics", {}),
            },
            warnings=[*asset_report.get("warnings", []), *processing_report.get("warnings", [])],
            errors=[*asset_report.get("errors", []), *processing_report.get("errors", [])],
        )
    )


def _fail_processing(db: Session, version_id: str, job_id: str, exc: Exception) -> None:
    db.rollback()
    version = db.get(DatasetVersion, version_id)
    job = db.get(IngestionJob, job_id)
    if version:
        version.status = ProcessingStatus.failed.value
        version.quality_status = "processing_error"
        version.quality_summary = {"error": str(exc)}
    if job:
        job.status = "failed"
        job.stage = "processing_failed"
        job.error_message = str(exc)
        job.finished_at = utcnow()
    db.commit()


def ingest_asset(db: Session, source: DataSource, connector: SourceConnector, asset: SourceAsset, job: IngestionJob) -> DatasetVersion:
    version = db.scalar(
        select(DatasetVersion).where(
            DatasetVersion.source_id == source.id,
            DatasetVersion.source_asset_id == asset.source_asset_id,
        )
    )
    if version and not (
        version.status == ProcessingStatus.failed.value and version.quality_status == "processing_error"
    ):
        job.status = "completed"
        job.stage = "skipped"
        job.progress = 1.0
        job.finished_at = utcnow()
        db.commit()
        return version

    if version is None:
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
    else:
        version.status = ProcessingStatus.staged.value
        version.quality_status = "pending"
        version.quality_summary = {}
    job.dataset_version_id = version.id
    job.stage = "validation"
    job.progress = 0.3
    db.commit()

    version.status = ProcessingStatus.validating.value
    asset_report = connector.validate(asset)
    if not asset_report["passed"]:
        _validation_report(db, version, asset_report)
        version.status = ProcessingStatus.failed.value
        version.quality_status = "rejected"
        version.quality_summary = asset_report.get("metrics", {})
        job.status = "failed"
        job.stage = "validation_failed"
        job.error_message = "; ".join(asset_report.get("errors", []))
        job.finished_at = utcnow()
        db.commit()
        return version

    version.status = ProcessingStatus.processing.value
    version.ingested_at = utcnow()
    job.stage = "processing"
    job.progress = 0.55
    db.commit()
    try:
        area_inputs = _area_inputs(db)
        records = connector.process(asset, area_inputs) if connector.requires_area_processing else []
        processing_report = connector.validate_processed(asset, records)
        known_area_ids = {area["id"] for area in area_inputs}
        unknown_area_ids = sorted({record.area_id for record in records} - known_area_ids)
        if unknown_area_ids:
            processing_report = {
                **processing_report,
                "passed": False,
                "errors": [
                    *processing_report.get("errors", []),
                    "processing returned unknown area ids: %s" % ", ".join(unknown_area_ids),
                ],
            }
    except Exception as exc:
        _fail_processing(db, version.id, job.id, exc)
        raise

    _validation_report(db, version, asset_report, processing_report)
    if not processing_report["passed"]:
        version.status = ProcessingStatus.failed.value
        version.quality_status = "rejected"
        version.quality_summary = processing_report.get("metrics", {})
        job.status = "failed"
        job.stage = "validation_failed"
        job.error_message = "; ".join(processing_report.get("errors", []))
        job.finished_at = utcnow()
        db.commit()
        return version

    _persist_area_records(db, connector, version, records)
    if connector.layer_key == "fixture":
        _publish_fixture_indicators(db, version, asset.payload)
    version.status = ProcessingStatus.publishing.value
    version.quality_status = "valid"
    version.quality_summary = processing_report.get("metrics", {})
    version.validated_at = utcnow()
    job.stage = "publishing"
    job.progress = 0.8
    current_layer = db.scalar(
        select(func.max(DatasetVersion.layer_version)).where(
            DatasetVersion.source_id == source.id,
            DatasetVersion.id != version.id,
        )
    ) or 0
    version.layer_version = current_layer + 1
    version.status = ProcessingStatus.published.value
    version.published_at = utcnow()
    db.flush()
    priority_updates = recalculate_priority_scores(db) if records else 0

    for optimization in db.scalars(select(OptimizationRun).where(OptimizationRun.is_outdated.is_(False))).all():
        recorded = optimization.input_versions.get(source.dataset_id) if optimization.input_versions else None
        if recorded is None or recorded.get("version_id") != version.id:
            optimization.is_outdated = True
    affected_layers = {
        "temperature": ["temperature", "historical", "priority"],
        "ndvi": ["ndvi", "priority"],
        "landcover": ["vegetation", "built", "priority"],
        "population": ["population", "priority"],
        "urban_form": ["candidates", "priority"],
        "weather": ["weather"],
        "fixture": ["fixture"],
    }.get(connector.layer_key, [connector.layer_key])
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
                "affected_layers": affected_layers,
                "cache_tags": ["layer:%s" % layer for layer in affected_layers],
                "processed_areas": len(records),
                "priority_scores_recalculated": priority_updates,
            },
        )
    )
    job.status = "completed"
    job.stage = "published"
    job.progress = 1.0
    job.checkpoint = {
        "processed_areas": len(records),
        "layer_version": version.layer_version,
        "priority_scores_recalculated": priority_updates,
    }
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
                    select(DatasetVersion).where(
                        DatasetVersion.source_id == source.id,
                        DatasetVersion.source_asset_id == asset.source_asset_id,
                    )
                )
                retryable = duplicate and duplicate.status == ProcessingStatus.failed.value and duplicate.quality_status == "processing_error"
                if duplicate and not retryable:
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
            check.provider_quota = connector.quota_status()
            check.status = "success"
            check.completed_at = utcnow()
            db.commit()
            return {
                "status": "success",
                "source": source.name,
                "discovered": len(assets),
                "processed": len(processed),
                "job_ids": jobs,
            }
        except Exception as exc:
            db.rollback()
            check = db.get(SourceCheck, check.id)
            check.provider_quota = connector.quota_status()
            check.status = "failed"
            check.error_message = str(exc)
            check.completed_at = utcnow()
            db.commit()
            logger.exception("Source monitor failed for %s", source.name)
            raise
