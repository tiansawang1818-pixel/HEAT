import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.entities import (
    AreaIndicator,
    CandidateIntervention,
    DataSource,
    DatasetVersion,
    DistrictBoundary,
    GeographicArea,
    IngestionJob,
    OptimizationRun,
    PipelineEvent,
    PopulationEstimate,
    ProcessingStatus,
    PriorityScore,
    RasterAssetMetadata,
    SourceCheck,
    TemperatureObservation,
    ValidationReport,
    VegetationObservation,
)
from app.services.analytics import recalculate_priority_scores
from app.services.checkpoints import SourceCheckpointStore
from app.services.connectors.base import (
    AreaProcessingRecord,
    BoundaryProcessingRecord,
    CandidateProcessingRecord,
    ConnectorError,
    SourceAsset,
    SourceConnector,
)
from app.services.geometry import to_db_geometry, to_geojson
from app.services.locks import distributed_lock


logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _recover_interrupted_source(db: Session, source_id: str) -> None:
    """Called only after acquiring exclusive ownership of the source monitor.

    Publication is one transaction, so unfinished versions have no committed
    observations to remove. Preserve their IDs and make them retryable instead.
    """
    unfinished = [ProcessingStatus.staged.value, ProcessingStatus.validating.value,
                  ProcessingStatus.processing.value, ProcessingStatus.publishing.value]
    for version in db.scalars(select(DatasetVersion).where(
        DatasetVersion.source_id == source_id, DatasetVersion.status.in_(unfinished)
    )).all():
        version.status = ProcessingStatus.failed.value
        version.quality_status = "processing_error"
        version.quality_summary = {"recovery": "Previous source monitor ended before publication"}
    for job in db.scalars(select(IngestionJob).where(
        IngestionJob.source_id == source_id, IngestionJob.status == "running",
        IngestionJob.job_type.notin_(["scheduled_refresh", "manual_refresh", "historical_backfill"]),
    )).all():
        job.status, job.stage = "failed", "interrupted"
        job.error_message = "Previous worker ended before completion; asset is eligible for safe retry"
        job.finished_at = utcnow()
    for check in db.scalars(select(SourceCheck).where(
        SourceCheck.source_id == source_id, SourceCheck.status == "running"
    )).all():
        check.status = "failed"
        check.error_message = "Previous source monitor ended before completion"
        check.completed_at = utcnow()
    db.commit()


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


def _area_inputs(db: Session, simplify_degrees: Optional[float] = None) -> List[Dict[str, Any]]:
    """District geometries for aggregation.

    Connectors that aggregate rasters far coarser than the boundary survey can
    ask PostGIS for a topology-preserving simplification. This keeps the
    request built for a remote provider small on the memory-limited runtime.
    """
    if simplify_degrees and db.get_bind().dialect.name == "postgresql":
        geojson = func.ST_AsGeoJSON(func.ST_SimplifyPreserveTopology(GeographicArea.geometry, simplify_degrees), 7)
        return [
            {"id": area_id, "geometry": json.loads(value) if value else None}
            for area_id, value in db.execute(select(GeographicArea.id, geojson)).all()
        ]
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


def _normalized_area_name(value: str) -> str:
    return value.replace("(สาธิต)", "").replace("(demo)", "").strip().casefold()


def _persist_boundary_records(
    db: Session,
    version: DatasetVersion,
    records: Sequence[BoundaryProcessingRecord],
) -> None:
    if not records:
        return
    existing_areas = list(db.scalars(select(GeographicArea)).all())
    by_id = {area.id: area for area in existing_areas}
    by_name: Dict[str, GeographicArea] = {}
    for area in existing_areas:
        by_name[_normalized_area_name(area.name_th)] = area
        by_name[_normalized_area_name(area.name_en)] = area
    boundary_time = version.observed_at or utcnow()
    active_boundaries: Dict[str, List[DistrictBoundary]] = {}
    for boundary in db.scalars(select(DistrictBoundary).where(DistrictBoundary.valid_to.is_(None))):
        active_boundaries.setdefault(boundary.area_id, []).append(boundary)
    new_boundaries = []
    for record in records:
        area = by_id.get(record.area_id)
        geometry = to_db_geometry(record.geometry)
        if area is None:
            area = by_name.get(_normalized_area_name(record.name_th)) or by_name.get(
                _normalized_area_name(record.name_en)
            )
        if area is None:
            area = GeographicArea(
                id=record.area_id,
                name_th=record.name_th,
                name_en=record.name_en,
                geometry=geometry,
            )
            db.add(area)
        area.name_th = record.name_th
        area.name_en = record.name_en
        area.area_type = "district"
        area.geometry = geometry
        area.centroid = to_db_geometry(record.centroid)
        area.area_sq_km = record.area_sq_km
        area.source_name = "Bangkok Metropolitan Administration / data.go.th"
        area.source_version = "%s:%s" % (version.source_version, record.district_code)
        area.quality_status = "verified_official_boundary"
        for previous in active_boundaries.get(area.id, []):
            previous.valid_to = boundary_time
        new_boundaries.append(
            DistrictBoundary(
                area_id=area.id,
                dataset_version_id=version.id,
                geometry=geometry,
                valid_from=boundary_time,
            )
        )
    # With autoflush disabled and no mapped relationship, Postgres needs the
    # parent rows flushed first. Both sets of changes commit together below.
    db.flush()
    db.add_all(new_boundaries)


def _persist_candidate_records(
    db: Session,
    connector: SourceConnector,
    version: DatasetVersion,
    records: Sequence[CandidateProcessingRecord],
) -> None:
    latest_scores: Dict[str, float] = {}
    for record in records:
        if record.area_id in latest_scores:
            continue
        score = db.scalar(
            select(PriorityScore)
            .where(PriorityScore.area_id == record.area_id)
            .order_by(PriorityScore.calculated_at.desc())
            .limit(1)
        )
        latest_scores[record.area_id] = float(score.score) if score else 0.0

    current_ids = {record.candidate_id for record in records}
    existing_rows = list(db.scalars(select(CandidateIntervention)).all())
    existing_by_id = {row.id: row for row in existing_rows}
    for record in records:
        row = existing_by_id.get(record.candidate_id)
        if row is None:
            row = CandidateIntervention(
                id=record.candidate_id,
                area_id=record.area_id,
                name_th=record.name_th,
                intervention_type=record.intervention_type,
                geometry=to_db_geometry(record.geometry),
                estimated_cost_thb=None,
                annual_maintenance_thb=None,
                estimated_beneficiaries=None,
                priority_score=latest_scores[record.area_id],
                benefit_score=None,
                feasibility_status="insufficient_evidence",
                feasibility_constraints=record.feasibility_constraints,
                evidence=record.evidence,
                uncertainty=record.uncertainty,
                dataset_versions={},
                active=True,
            )
            db.add(row)
        else:
            row.area_id = record.area_id
            row.name_th = record.name_th
            row.intervention_type = record.intervention_type
            row.geometry = to_db_geometry(record.geometry)
            row.priority_score = latest_scores[record.area_id]
            row.feasibility_constraints = {
                **record.feasibility_constraints,
                **(row.feasibility_constraints or {}),
            }
            existing_evidence = row.evidence or {}
            approved_evidence = {
                key: existing_evidence[key]
                for key in (
                    "cost_assumption_id",
                    "cost_evidence",
                    "benefit_evidence",
                    "feasibility_evidence",
                    "approved_at",
                    "approved_by",
                )
                if existing_evidence.get(key) is not None
            }
            row.evidence = {**existing_evidence, **record.evidence, **approved_evidence}
            row.uncertainty = {**record.uncertainty, **(row.uncertainty or {})}
            row.active = True
        row.dataset_versions = {
            **(row.dataset_versions or {}),
            connector.dataset_id: {
                "version_id": version.id,
                "source_version": version.source_version,
                "source_asset_id": version.source_asset_id,
                "observed_at": version.observed_at.isoformat() if version.observed_at else None,
            },
        }

    for row in existing_rows:
        evidence = row.evidence if isinstance(row.evidence, dict) else {}
        if evidence.get("source_dataset_id") == connector.dataset_id and row.id not in current_ids:
            row.active = False


def _persist_raster_metadata(
    db: Session,
    connector: SourceConnector,
    version: DatasetVersion,
    asset: SourceAsset,
) -> None:
    if not connector.produces_raster:
        return
    metadata = connector.raster_metadata(asset)
    if not metadata:
        raise ValueError("Raster-producing connector did not return raster metadata")
    db.add(
        RasterAssetMetadata(
            dataset_version_id=version.id,
            storage_uri=metadata["storage_uri"],
            media_type=metadata["media_type"],
            crs=metadata["crs"],
            bounds=metadata["bounds"],
            resolution_m=metadata.get("resolution_m"),
            band_metadata=metadata.get("band_metadata", {}),
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
    retry_rejected = bool(
        version and version.status == ProcessingStatus.failed.value and version.quality_status == "rejected"
        and asset.metadata.get("processor_version")
        and (version.metadata_json or {}).get("processor_version") != asset.metadata.get("processor_version")
    )
    if version and not retry_rejected and not (
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
        version.checksum = asset.checksum
        version.metadata_json = {**(version.metadata_json or {}), **asset.metadata}
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
        area_inputs = _area_inputs(db, getattr(connector, "analysis_geometry_simplify_degrees", None))
        records: List[AreaProcessingRecord] = []
        boundary_records: List[BoundaryProcessingRecord] = []
        candidate_records: List[CandidateProcessingRecord] = []
        if connector.requires_boundary_processing:
            boundary_records = connector.process_boundaries(asset)
            processing_report = connector.validate_boundaries(asset, boundary_records)
            if boundary_records:
                version.checksum = boundary_records[0].source_checksum
        elif connector.requires_candidate_processing:
            candidate_records = connector.process_candidates(asset, area_inputs)
            processing_report = connector.validate_candidates(asset, candidate_records)
            known_area_ids = {area["id"] for area in area_inputs}
            unknown_area_ids = sorted({record.area_id for record in candidate_records} - known_area_ids)
            if unknown_area_ids:
                processing_report = {
                    **processing_report,
                    "passed": False,
                    "errors": [
                        *processing_report.get("errors", []),
                        "processing returned unknown area ids: %s" % ", ".join(unknown_area_ids),
                    ],
                }
        else:
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

    try:
        return _publish_asset(db, source, connector, asset, job, version, records, boundary_records,
                              candidate_records, processing_report)
    except SQLAlchemyError as exc:
        error = ConnectorError("Validated dataset publication failed (%s)" % type(exc).__name__)
        _fail_processing(db, version.id, job.id, error)
        raise error from exc


def _publish_asset(
    db: Session, source: DataSource, connector: SourceConnector, asset: SourceAsset,
    job: IngestionJob, version: DatasetVersion, records: Sequence[AreaProcessingRecord],
    boundary_records: Sequence[BoundaryProcessingRecord], candidate_records: Sequence[CandidateProcessingRecord],
    processing_report: Dict[str, Any],
) -> DatasetVersion:
    _persist_area_records(db, connector, version, records)
    _persist_boundary_records(db, version, boundary_records)
    _persist_candidate_records(db, connector, version, candidate_records)
    _persist_raster_metadata(db, connector, version, asset)
    if connector.layer_key == "fixture" and isinstance(asset.payload, dict):
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
    processed_area_count = len(records) + len(boundary_records)
    processed_candidate_count = len(candidate_records)

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
        "boundaries": ["temperature", "population", "vegetation", "ndvi", "built", "priority", "historical", "future-risk"],
        "weather": ["weather"],
        "fixture": ["fixture"],
    }.get(connector.layer_key, [connector.layer_key])
    if connector.layer_key == "fixture" and isinstance(asset.payload, dict):
        fixture_layer_by_indicator = {"lst_c_mean": "temperature", "ndvi": "ndvi", "tree_cover_fraction": "vegetation", "built_fraction": "built", "population_density": "population"}
        affected_layers = sorted({
            "fixture",
            *(fixture_layer_by_indicator[entry["indicator_key"]]
              for entry in asset.payload.get("area_indicators", [])
              if isinstance(entry, dict) and entry.get("indicator_key") in fixture_layer_by_indicator),
        })
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
                "processed_areas": processed_area_count,
                "processed_candidates": processed_candidate_count,
                "priority_scores_recalculated": priority_updates,
            },
        )
    )
    job.status = "completed"
    job.stage = "published"
    job.progress = 1.0
    job.checkpoint = {
        "processed_areas": processed_area_count,
        "processed_candidates": processed_candidate_count,
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
        _recover_interrupted_source(db, source.id)
        check = SourceCheck(source_id=source.id)
        db.add(check)
        db.commit()
        latest = _latest_version(db, source.id)
        try:
            connector.bind_checkpoints(SourceCheckpointStore(db, source.id, getattr(connector, "processor_version", "v1")))
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
            check.error_message = connector.monitor_warning()
            check.status = "degraded" if check.error_message else "success"
            check.completed_at = utcnow()
            db.commit()
            connector.checkpoint_store.clear()
            return {
                "status": check.status,
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
