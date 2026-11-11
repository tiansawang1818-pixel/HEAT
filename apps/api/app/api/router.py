import hashlib
import html
import json
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer

from app.api.deps import require_admin
from app.api.backfills import router as backfill_router
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import (
    AreaIndicator,
    CandidateIntervention,
    CostAssumption,
    DataSource,
    DatasetVersion,
    ForecastRun,
    GeographicArea,
    IngestionJob,
    OptimizationAllocation,
    OptimizationRun,
    PipelineEvent,
    ProcessingStatus,
    PriorityScore,
    SavedScenario,
    SourceCheck,
    TemperatureObservation,
    ValidationReport,
)
from app.schemas.api import (
    AssistantQuery,
    CandidateApprovalRequest,
    CostAssumptionCreateRequest,
    ForecastRequest,
    IngestionRequest,
    OptimizationRequest,
    PriorityCalculateRequest,
    ScenarioCompareRequest,
    ScenarioSaveRequest,
)
from app.services.connectors import (
    BangkokBoundaryConnector,
    DynamicWorldConnector,
    FixtureConnector,
    LandsatConnector,
    OpenMeteoConnector,
    OpenStreetMapConnector,
    SentinelConnector,
    WorldPopConnector,
)
from app.services.optimizer import Candidate, baseline_portfolio, eligible_candidates, optimize_portfolio
from app.services.pipeline import run_monitor
from app.services.priority import calculate_priority, sensitivity_range
from app.services.geometry import to_geojson
from app.services.tiles import earth_engine_tiles
from app.services.observability import prometheus_metrics
from app.services.readiness import production_readiness
from app.services.runtime_status import runtime_status
from app.services.forecast_workflow import ForecastInProgress, compute_forecast
from app.services.data_readiness import published_data_readiness
from app.services.locks import LockBackendUnavailable
from app.services.versions import INDICATOR_DATASETS, LAYER_DATASETS, current_versions, published_version


router = APIRouter()
router.include_router(backfill_router)


def _report_number(value, digits=0):
    return "—" if value is None else format(float(value), ",.%df" % digits)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return _aware(value).isoformat() if value else None


def _ingestion_error_summary(value: Optional[str]) -> Optional[str]:
    if value and "\n[SQL:" in value:
        return "การบันทึกข้อมูลลงฐานข้อมูลไม่สำเร็จ"
    return value


def _parse_bbox(value: Optional[str]) -> Optional[tuple[float, float, float, float]]:
    if not value:
        return None
    try:
        west, south, east, north = (float(part.strip()) for part in value.split(","))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="bbox must be west,south,east,north")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(status_code=422, detail="bbox coordinates are invalid or out of order")
    return west, south, east, north


def _geometry_intersects_bbox(geometry: Any, bbox: Optional[tuple[float, float, float, float]]) -> bool:
    """Bounds check used by SQLite fixtures and the Bangkok pilot API.

    A large production deployment should push the equivalent predicate down to
    PostGIS with ST_Intersects or serve MVTs instead of loading all geometries.
    """
    if bbox is None:
        return True
    geometry = to_geojson(geometry)
    if not geometry or "coordinates" not in geometry:
        return False
    points: List[tuple[float, float]] = []

    def visit(value: Any) -> None:
        if isinstance(value, (list, tuple)):
            if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
                points.append((float(value[0]), float(value[1])))
            else:
                for item in value:
                    visit(item)

    visit(geometry["coordinates"])
    if not points:
        return False
    geom_west = min(point[0] for point in points)
    geom_south = min(point[1] for point in points)
    geom_east = max(point[0] for point in points)
    geom_north = max(point[1] for point in points)
    west, south, east, north = bbox
    return geom_west <= east and geom_east >= west and geom_south <= north and geom_north >= south


def _viewport_rows(
    db: Session,
    entity: Any,
    bbox: Optional[tuple[float, float, float, float]],
    limit: int,
    *filters: Any,
    defer_geometry: bool = False,
) -> List[Any]:
    query = select(entity)
    if defer_geometry and settings.database_url.startswith("postgresql"):
        query = query.options(defer(entity.geometry))
    if filters:
        query = query.where(*filters)
    if bbox and settings.database_url.startswith("postgresql"):
        west, south, east, north = bbox
        query = query.where(
            func.ST_Intersects(entity.geometry, func.ST_MakeEnvelope(west, south, east, north, 4326))
        )
    query = query.order_by(entity.id)
    if settings.database_url.startswith("postgresql") or bbox is None:
        query = query.limit(limit)
    rows = list(db.scalars(query).all())
    if bbox and not settings.database_url.startswith("postgresql"):
        rows = [row for row in rows if _geometry_intersects_bbox(row.geometry, bbox)][:limit]
    return rows


# About 5 m at Bangkok's latitude: below the map's district-scale display
# resolution, and far smaller than the full-detail boundary payload.
MAP_DISPLAY_SIMPLIFY_DEGREES = 0.00005


def _display_geometries(db: Session, entity: Any, ids: List[str], multi: bool = False) -> Dict[str, Optional[Dict[str, Any]]]:
    """Return simplified display GeoJSON rendered by PostGIS, keyed by row id.

    Analysis always uses the stored geometry; only the map payload is simplified.
    """
    if not ids or not settings.database_url.startswith("postgresql"):
        return {}
    simplified = func.ST_SimplifyPreserveTopology(entity.geometry, MAP_DISPLAY_SIMPLIFY_DEGREES)
    if multi:
        # GEOS returns a single-part MultiPolygon as Polygon; keep the API's
        # MultiPolygon contract for district boundaries.
        simplified = func.ST_Multi(simplified)
    geojson = func.ST_AsGeoJSON(simplified, 6)
    return {
        row_id: json.loads(value) if value else None
        for row_id, value in db.execute(select(entity.id, geojson).where(entity.id.in_(ids))).all()
    }


def _latest_source_rows(db: Session, model: Any, source_ids: List[str], order_column: Any, *filters: Any) -> Dict[str, Any]:
    if not source_ids:
        return {}
    ranked = select(model.id, func.row_number().over(
        partition_by=model.source_id,
        order_by=(order_column.desc().nulls_last(), model.id.desc()),
    ).label("source_rank")).where(model.source_id.in_(source_ids), *filters).subquery()
    rows = db.scalars(select(model).join(ranked, model.id == ranked.c.id).where(ranked.c.source_rank == 1)).all()
    return {row.source_id: row for row in rows}


def _source_statuses(db: Session, sources: List[DataSource]) -> List[Dict[str, Any]]:
    source_ids = [source.id for source in sources]
    latest_any = _latest_source_rows(db, DatasetVersion, source_ids, DatasetVersion.discovered_at)
    published = _latest_source_rows(db, DatasetVersion, source_ids, DatasetVersion.observed_at, DatasetVersion.status == "published")
    checks = _latest_source_rows(db, SourceCheck, source_ids, SourceCheck.checked_at)
    now = datetime.now(timezone.utc)
    return [_format_source_status(source, latest_any.get(source.id), published.get(source.id), checks.get(source.id), now) for source in sources]


def _source_status(db: Session, source: DataSource) -> Dict[str, Any]:
    return _source_statuses(db, [source])[0]


def _cloud_obscured(source: DataSource, version: DatasetVersion) -> bool:
    """A satellite scene rejected only because no district had clear pixels."""
    return (source.source_type in {"satellite", "land_cover"} and version.quality_status == "rejected"
            and (version.quality_summary or {}).get("processed_areas") == 0)


def _freshness_status(source: DataSource, latest: DatasetVersion, now: datetime) -> tuple:
    if latest.observed_at and (now - _aware(latest.observed_at)).total_seconds() > source.stale_after_seconds:
        return "stale", "อายุข้อมูลเกินเกณฑ์ %s ชั่วโมง" % round(source.stale_after_seconds / 3600)
    return "up_to_date", "เป็นเวอร์ชันล่าสุดที่ตรวจสอบและเผยแพร่แล้ว"


def _format_source_status(source: DataSource, latest_any: Optional[DatasetVersion], latest: Optional[DatasetVersion],
                          check: Optional[SourceCheck], now: datetime) -> Dict[str, Any]:
    reason = "ยังไม่มีข้อมูลที่ผ่านการตรวจสอบ"
    status = "unavailable"
    if latest_any and latest_any.status in {"staged", "validating", "processing", "publishing"}:
        status, reason = "updating", "กำลังตรวจสอบและประมวลผลเวอร์ชันใหม่"
    elif latest_any and latest_any.status == "failed" and _cloud_obscured(source, latest_any) and latest:
        status, reason = _freshness_status(source, latest, now)
        if status == "up_to_date":
            reason = "ภาพล่าสุดถูกเมฆบังทุกเขต ระบบใช้ภาพที่ชัดก่อนหน้า"
    elif latest_any and latest_any.status == "failed" and (not latest or _aware(latest_any.discovered_at) > _aware(latest.published_at)):
        status, reason = "error", "เวอร์ชันล่าสุดไม่ผ่านการตรวจสอบ; ระบบยังคงใช้เวอร์ชันก่อนหน้า"
    elif check and check.status == "failed" and latest and latest.published_at and _aware(check.checked_at) > _aware(latest.published_at):
        # Validated data is still served, so an unreachable provider degrades
        # the source rather than breaking it; readiness treats it as a warning.
        status, reason = "stale", "เชื่อมต่อผู้ให้บริการครั้งล่าสุดไม่สำเร็จ ระบบใช้ข้อมูลที่ตรวจแล้วล่าสุดและจะลองใหม่เอง"
    elif check and check.status == "failed" and not latest:
        status, reason = "error", "ตรวจสอบผู้ให้บริการครั้งล่าสุดไม่สำเร็จ และยังไม่มีข้อมูลที่ผ่านการตรวจ"
    elif check and check.status == "degraded" and latest:
        status, reason = "stale", "ยังตรวจสอบต้นทาง กทม. ไม่ได้ ระบบแสดงสำเนาขอบเขตจากแค็ตตาล็อกข้อมูลเปิดของรัฐที่ผ่านการตรวจสอบ"
    elif latest and latest.observed_at:
        status, reason = _freshness_status(source, latest, now)
    elif check and check.status == "failed":
        status, reason = "error", check.error_message or "เชื่อมต่อผู้ให้บริการไม่สำเร็จ"
    next_check = _aware(check.checked_at) + timedelta(seconds=source.check_interval_seconds) if check and source.check_interval_seconds else None
    return {
        "id": source.id,
        "source_name": source.name,
        "provider": source.provider,
        "dataset_id": source.dataset_id,
        "status": status,
        "status_reason": reason,
        "last_source_observation": _iso(latest.observed_at) if latest else None,
        "last_system_check": _iso(check.checked_at) if check else None,
        "last_ingested_at": _iso(latest.ingested_at) if latest else None,
        "last_validated_at": _iso(latest.validated_at) if latest else None,
        "last_published_at": _iso(latest.published_at) if latest else None,
        "next_check_at": _iso(next_check),
        "processing_status": latest_any.status if latest_any else "idle",
        "quality_status": latest.quality_status if latest else "unavailable",
        "geographic_extent": latest.geographic_extent if latest else None,
        "layer_version": latest.layer_version if latest else 0,
        "source_version": latest.source_version if latest else None,
    }


@router.get("/health")
def health(db: Session = Depends(get_db)) -> Dict[str, Any]:
    db.execute(select(1))
    return {"status": "ok", "service": settings.app_name, "time": datetime.now(timezone.utc).isoformat()}


@router.get("/admin/identity")
def admin_identity(response: Response, actor: str = Depends(require_admin)) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True, "actor": actor}


@router.get("/health/live")
def health_live() -> Dict[str, Any]:
    return {"status": "ok", "service": settings.app_name, "time": datetime.now(timezone.utc).isoformat()}


@router.get("/health/ready")
def health_ready(response: Response, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        db.execute(select(1))
        enabled_sources = db.scalar(
            select(func.count()).select_from(DataSource).where(DataSource.enabled.is_(True))
        ) or 0
    except Exception:
        response.status_code = 503
        return {"status": "not_ready", "database": "unavailable", "time": datetime.now(timezone.utc).isoformat()}
    if enabled_sources == 0:
        response.status_code = 503
        return {"status": "not_ready", "database": "ready", "enabled_sources": 0, "time": datetime.now(timezone.utc).isoformat()}
    return {
        "status": "ready",
        "database": "ready",
        "enabled_sources": enabled_sources,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/metrics", include_in_schema=False)
def metrics(db: Session = Depends(get_db)) -> Response:
    return Response(
        content=prometheus_metrics(db),
        media_type="text/plain; version=0.0.4",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/datasets")
def datasets(db: Session = Depends(get_db)) -> Dict[str, Any]:
    sources = db.scalars(select(DataSource).where(DataSource.source_type != "test_fixture").order_by(DataSource.name)).all()
    return {"items": _source_statuses(db, sources), "count": len(sources)}


# Dashboard reads (dataset status, layer list, weather) each run several
# queries against a database ~0.5-1 s away. One round trip reads a
# generation stamp that moves with every publication, source check and job;
# while it is unchanged the encoded response is reused for a short time.
_RESPONSE_CACHE: Dict[str, tuple] = {}
_RESPONSE_CACHE_SECONDS = 30
_RESPONSE_BUILD_LOCKS: Dict[str, threading.Lock] = {}


def _dashboard_generation(db: Session) -> tuple:
    return tuple(db.execute(select(
        select(func.count(PipelineEvent.id)).scalar_subquery(),
        select(func.max(PipelineEvent.created_at)).scalar_subquery(),
        select(func.max(SourceCheck.checked_at)).scalar_subquery(),
        select(func.max(SourceCheck.completed_at)).scalar_subquery(),
        select(func.max(IngestionJob.created_at)).scalar_subquery(),
        select(func.max(IngestionJob.finished_at)).scalar_subquery(),
    )).one())


def _cached_dashboard_response(db: Session, name: str, build, seconds: int = _RESPONSE_CACHE_SECONDS) -> Response:
    key = "%s|%s" % (name, _dashboard_generation(db))
    entry = _RESPONSE_CACHE.get(name)
    if entry and entry[0] == key and entry[1] > time.monotonic():
        body = entry[2]
    else:
        lock = _RESPONSE_BUILD_LOCKS.setdefault(name, threading.Lock())
        if entry and not lock.acquire(blocking=False):
            # Another request is rebuilding this response; serve the previous one
            # instead of holding a second database connection for the same work.
            body = entry[2]
        else:
            if not entry:
                # Nothing to serve yet: wait for the builder without pinning a
                # pooled connection, then reuse what it built.
                db.close()
                lock.acquire()
            try:
                entry = _RESPONSE_CACHE.get(name)
                if entry and entry[0] == key and entry[1] > time.monotonic():
                    body = entry[2]
                else:
                    body = json.dumps(build(), ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
                    _RESPONSE_CACHE[name] = (key, time.monotonic() + seconds, body)
            finally:
                lock.release()
    return Response(content=body, media_type="application/json", headers={"Cache-Control": "no-cache"})


@router.get("/datasets/status")
def dataset_status(db: Session = Depends(get_db)) -> Response:
    return _cached_dashboard_response(db, "datasets/status", lambda: _dataset_status(db))


def _dataset_status(db: Session) -> Dict[str, Any]:
    sources = db.scalars(select(DataSource).where(DataSource.source_type != "test_fixture").order_by(DataSource.name)).all()
    items = _source_statuses(db, sources)
    return {
        "items": items,
        "summary": {status: sum(1 for item in items if item["status"] == status) for status in ["up_to_date", "updating", "stale", "unavailable", "error"]},
    }


@router.get("/weather/context")
def weather_context(db: Session = Depends(get_db)) -> Response:
    return _cached_dashboard_response(db, "weather/context", lambda: _weather_context(db))


def _weather_context(db: Session) -> Dict[str, Any]:
    """Return only the latest validated Open-Meteo context.

    Values are model estimates and forecasts, not satellite land-surface
    temperature observations or ground-station measurements.
    """
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == OpenMeteoConnector.dataset_id))
    if source is None:
        return {
            "status": "unavailable",
            "status_reason": "ยังไม่ได้ตั้งค่าแหล่งข้อมูล Open-Meteo",
            "source_name": "Open-Meteo",
            "dataset_id": OpenMeteoConnector.dataset_id,
            "data_class": "model_estimate_and_forecast",
            "current": None,
            "current_units": {},
            "hourly": None,
            "hourly_units": {},
            "reference_time": None,
            "provider_model": None,
            "provider_timezone": None,
            "layer_version": 0,
            "limitations_th": "ข้อมูลนี้เป็นค่าประมาณจากแบบจำลองและพยากรณ์ ไม่ใช่อุณหภูมิพื้นผิวจากดาวเทียมหรือสถานีภาคพื้นดิน",
        }
    status = _source_status(db, source)
    version = db.scalar(
        select(DatasetVersion)
        .where(DatasetVersion.source_id == source.id, DatasetVersion.status == ProcessingStatus.published.value)
        .order_by(desc(DatasetVersion.observed_at))
        .limit(1)
    )
    if version is None:
        return {
            **status,
            "data_class": "model_estimate_and_forecast",
            "current": None,
            "current_units": {},
            "hourly": None,
            "hourly_units": {},
            "reference_time": None,
            "provider_model": None,
            "provider_timezone": None,
            "limitations_th": "ข้อมูลนี้เป็นค่าประมาณจากแบบจำลองและพยากรณ์ ไม่ใช่อุณหภูมิพื้นผิวจากดาวเทียมหรือสถานีภาคพื้นดิน",
        }
    metadata = version.metadata_json if isinstance(version.metadata_json, dict) else {}
    payload = metadata.get("provider_payload", {})
    if not isinstance(payload, dict):
        payload = {}
    return {
        **status,
        "data_class": metadata.get("data_class", "model_estimate_and_forecast"),
        "reference_time": metadata.get("reference_time") or _iso(version.observed_at),
        "provider_model": metadata.get("provider_model"),
        "provider_timezone": metadata.get("provider_timezone"),
        "current": payload.get("current") if isinstance(payload.get("current"), dict) else None,
        "current_units": payload.get("current_units") if isinstance(payload.get("current_units"), dict) else {},
        "hourly": payload.get("hourly") if isinstance(payload.get("hourly"), dict) else None,
        "hourly_units": payload.get("hourly_units") if isinstance(payload.get("hourly_units"), dict) else {},
        "limitations_th": "ข้อมูลนี้เป็นค่าประมาณจากแบบจำลองและพยากรณ์ ไม่ใช่อุณหภูมิพื้นผิวจากดาวเทียมหรือสถานีภาคพื้นดิน",
    }


@router.get("/datasets/{source_id}/versions")
def dataset_versions(
    source_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    source = db.get(DataSource, source_id)
    if source is None or source.source_type == "test_fixture":
        raise HTTPException(status_code=404, detail="Data source not found")
    query = select(DatasetVersion).where(DatasetVersion.source_id == source.id)
    count_query = select(func.count()).select_from(DatasetVersion).where(DatasetVersion.source_id == source.id)
    if status:
        query = query.where(DatasetVersion.status == status)
        count_query = count_query.where(DatasetVersion.status == status)
    total = int(db.scalar(count_query) or 0)
    rows = db.scalars(
        query.order_by(desc(DatasetVersion.discovered_at)).offset((page - 1) * page_size).limit(page_size)
    ).all()
    version_ids = [row.id for row in rows]
    reports: Dict[str, ValidationReport] = {}
    if version_ids:
        for report in db.scalars(
            select(ValidationReport)
            .where(ValidationReport.dataset_version_id.in_(version_ids))
            .order_by(desc(ValidationReport.created_at))
        ).all():
            reports.setdefault(report.dataset_version_id, report)

    def serialize(row: DatasetVersion) -> Dict[str, Any]:
        report = reports.get(row.id)
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        return {
            "id": row.id,
            "source_asset_id": row.source_asset_id,
            "source_version": row.source_version,
            "checksum": row.checksum,
            "status": row.status,
            "quality_status": row.quality_status,
            "quality_summary": row.quality_summary,
            "layer_version": row.layer_version,
            "observed_at": _iso(row.observed_at),
            "discovered_at": _iso(row.discovered_at),
            "ingested_at": _iso(row.ingested_at),
            "validated_at": _iso(row.validated_at),
            "published_at": _iso(row.published_at),
            "geographic_extent": row.geographic_extent,
            "metadata": {key: value for key, value in metadata.items() if key != "provider_payload"},
            "provider_payload_preserved": "provider_payload" in metadata,
            "validation": None
            if report is None
            else {
                "passed": report.passed,
                "checks": report.checks,
                "warnings": report.warnings,
                "errors": report.errors,
                "created_at": _iso(report.created_at),
            },
        }

    return {
        "source": {
            "id": source.id,
            "source_name": source.name,
            "provider": source.provider,
            "dataset_id": source.dataset_id,
        },
        "items": [serialize(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/areas")
def areas(
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    query = select(GeographicArea)
    count_query = select(func.count()).select_from(GeographicArea)
    if search:
        condition = or_(GeographicArea.name_th.ilike("%%%s%%" % search), GeographicArea.name_en.ilike("%%%s%%" % search))
        query, count_query = query.where(condition), count_query.where(condition)
    total = db.scalar(count_query) or 0
    rows = db.scalars(query.order_by(GeographicArea.name_th).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "items": [
            {
                "id": row.id,
                "name_th": row.name_th,
                "name_en": row.name_en,
                "area_type": row.area_type,
                "centroid": to_geojson(row.centroid),
                "area_sq_km": row.area_sq_km,
                "quality_status": row.quality_status,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def _latest_indicators(db: Session, area_id: str, version_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    query = select(AreaIndicator).where(AreaIndicator.area_id == area_id)
    if version_id:
        query = query.where(AreaIndicator.dataset_version_id == version_id)
    rows = db.scalars(query.order_by(desc(AreaIndicator.observed_at))).all()
    result: Dict[str, Dict[str, Any]] = {}
    versions = {} if version_id else current_versions(db)
    for row in rows:
        active = versions.get(INDICATOR_DATASETS.get(row.indicator_key, ""))
        if active and row.dataset_version_id != active["version_id"]:
            continue
        if row.indicator_key not in result:
            result[row.indicator_key] = {
                "value": row.value,
                "unit": row.unit,
                "observed_at": _iso(row.observed_at),
                "quality_status": row.quality_status,
                "dataset_version_id": row.dataset_version_id,
            }
    return result


@router.get("/areas/{area_id}")
def area_detail(area_id: str, dataset_version_id: Optional[str] = Query(default=None, max_length=64), db: Session = Depends(get_db)) -> Dict[str, Any]:
    area = db.get(GeographicArea, area_id)
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    if dataset_version_id:
        version = db.get(DatasetVersion, dataset_version_id)
        if not version or version.status != "published":
            raise HTTPException(status_code=404, detail="Published dataset version not found")
    score = db.scalar(select(PriorityScore).where(PriorityScore.area_id == area_id).order_by(desc(PriorityScore.calculated_at)).limit(1))
    candidates = db.scalars(select(CandidateIntervention).where(CandidateIntervention.area_id == area_id, CandidateIntervention.active.is_(True))).all()
    return {
        "id": area.id,
        "name_th": area.name_th,
        "name_en": area.name_en,
        "geometry": to_geojson(area.geometry),
        "centroid": to_geojson(area.centroid),
        "boundary_provenance": {"source": area.source_name, "version": area.source_version, "quality_status": area.quality_status},
        "indicators": _latest_indicators(db, area_id, dataset_version_id),
        "priority": None
        if not score or dataset_version_id
        else {
            "score": score.score,
            "components": score.components,
            "weights": score.weights,
            "missing_inputs": score.missing_inputs,
            "formula_version": score.formula_version,
            "calculated_at": _iso(score.calculated_at),
        },
        "candidate_interventions": [
            {
                "id": item.id,
                "name_th": item.name_th,
                "intervention_type": item.intervention_type,
                "estimated_cost_thb": item.estimated_cost_thb,
                "estimated_beneficiaries": item.estimated_beneficiaries,
                "feasibility_status": item.feasibility_status,
                "evidence": item.evidence,
                "uncertainty": item.uncertainty,
            }
            for item in candidates
        ],
    }


@router.get("/areas/{area_id}/history")
def area_history(
    area_id: str,
    indicator: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=2000),
    dataset_version_id: Optional[str] = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if not db.get(GeographicArea, area_id):
        raise HTTPException(status_code=404, detail="Area not found")
    query = select(AreaIndicator).where(AreaIndicator.area_id == area_id)
    if dataset_version_id:
        version = db.get(DatasetVersion, dataset_version_id)
        if not version or version.status != "published" or not version.observed_at:
            raise HTTPException(status_code=404, detail="Published observation version not found")
        query = query.where(AreaIndicator.observed_at <= version.observed_at)
    if indicator:
        query = query.where(AreaIndicator.indicator_key == indicator)
    rows = db.scalars(query.order_by(desc(AreaIndicator.observed_at)).limit(limit)).all()
    return {
        "area_id": area_id,
        "items": [
            {
                "indicator": row.indicator_key,
                "value": row.value,
                "unit": row.unit,
                "observed_at": _iso(row.observed_at),
                "quality_status": row.quality_status,
                "dataset_version_id": row.dataset_version_id,
            }
            for row in rows
        ],
    }


LAYER_DEFINITIONS = [
    ("temperature", "อุณหภูมิพื้นผิว", "lst_c_mean", "°C"),
    ("population", "ความหนาแน่นประชากร", "population_density", "คน/ตร.กม."),
    ("vegetation", "พื้นที่พืชพรรณ", "tree_cover_fraction", "%"),
    ("ndvi", "ดัชนีพืชพรรณ NDVI", "ndvi", "index"),
    ("built", "พื้นที่สิ่งปลูกสร้าง", "built_fraction", "%"),
    ("priority", "ลำดับความสำคัญการรับความร้อน", "priority_score", "0–100"),
    ("candidates", "พื้นที่แทรกแซงที่เป็นไปได้", "candidate", "project"),
    ("historical", "การเปลี่ยนแปลงย้อนหลัง", "temperature_trend", "°C/ปี"),
    ("future-risk", "สถานการณ์ความเสี่ยงอนาคต", "scenario_change_c", "°C change"),
]


@router.get("/map/layers")
def map_layers(db: Session = Depends(get_db)) -> Response:
    return _cached_dashboard_response(db, "map/layers", lambda: _map_layers(db))


def _map_layers(db: Session) -> Dict[str, Any]:
    versions = {item["dataset_id"]: item for item in datasets(db)["items"]}
    source_ids = {
        "temperature": ["LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2"],
        "population": ["worldpop/global"],
        "vegetation": ["GOOGLE/DYNAMICWORLD/V1"],
        "ndvi": ["COPERNICUS/S2_SR_HARMONIZED"],
        "built": ["GOOGLE/DYNAMICWORLD/V1"],
        "priority": ["LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2", "COPERNICUS/S2_SR_HARMONIZED", "GOOGLE/DYNAMICWORLD/V1", "worldpop/global"],
        "candidates": ["osm/bangkok"],
        "historical": ["LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2"],
        "future-risk": [],
    }
    source_labels = {
        "temperature": "Landsat 8/9",
        "population": "WorldPop",
        "vegetation": "Google Dynamic World",
        "ndvi": "Sentinel-2",
        "built": "Google Dynamic World",
        "priority": "HEAT//SHIFT derived score",
        "candidates": "OpenStreetMap / approved candidate evidence",
        "historical": "Landsat 8/9",
        "future-risk": "HEAT//SHIFT scenario model",
    }

    def layer_has_data(layer: str, indicator: str) -> bool:
        if layer == "priority":
            return bool(db.scalar(select(func.count()).select_from(PriorityScore)))
        if layer == "candidates":
            return bool(db.scalar(select(func.count()).select_from(CandidateIntervention).where(CandidateIntervention.active.is_(True))))
        if layer == "historical":
            return bool(db.scalar(select(func.count()).select_from(TemperatureObservation)))
        if layer == "future-risk":
            latest_forecast = db.scalar(select(ForecastRun).order_by(desc(ForecastRun.created_at)).limit(1))
            return bool(latest_forecast and latest_forecast.results and latest_forecast.results.get("projections"))
        return bool(db.scalar(select(func.count()).select_from(AreaIndicator).where(
            AreaIndicator.indicator_key == indicator,
            AreaIndicator.quality_status.notin_(["synthetic_demo", "demo_only", "test_only"]),
        )))

    items = []
    for key, name, indicator, unit in LAYER_DEFINITIONS:
        relevant = {dataset_id: versions[dataset_id] for dataset_id in source_ids[key] if dataset_id in versions}
        has_data = layer_has_data(key, indicator)
        status = next((state for state in ["error", "updating", "stale", "unavailable", "up_to_date"]
                       if any(item["status"] == state for item in relevant.values())), "up_to_date" if has_data else "unavailable")
        if not has_data and status not in {"updating", "error"}:
            status = "unavailable"
        reasons = list(dict.fromkeys(item["status_reason"] for item in relevant.values() if item["status"] == status))
        items.append({
            "id": key,
            "name_th": name,
            "indicator_key": indicator,
            "unit": unit,
            "feature_url": "/api/map/features?layer=%s" % key,
            "tilejson_url": "/api/map/tiles/%s" % key if key in {"temperature", "vegetation", "ndvi", "built"} else None,
            "delivery": "raster_xyz_with_viewport_geojson_fallback" if key in {"temperature", "vegetation", "ndvi", "built"} else "viewport_geojson",
            "source_name": source_labels[key],
            "status": status,
            "status_reason": " · ".join(reasons) if reasons else "มีข้อมูลที่ผ่านการเผยแพร่" if has_data else "ยังไม่มีข้อมูลจริงที่ผ่านการเผยแพร่สำหรับชั้นนี้",
            "source_status": relevant,
        })
    return {"items": items}


@router.get("/map/observations")
def map_observations(layer: str, limit: int = Query(default=90, ge=1, le=200), db: Session = Depends(get_db)) -> Dict[str, Any]:
    dataset_id = LAYER_DATASETS.get(layer)
    if not dataset_id:
        return {"items": [], "supports_snapshots": False}
    rows = db.scalars(select(DatasetVersion).join(DataSource).where(
        DataSource.dataset_id == dataset_id, DatasetVersion.status == "published", DatasetVersion.observed_at.is_not(None),
    ).order_by(desc(DatasetVersion.observed_at), desc(DatasetVersion.published_at)).limit(limit)).all()
    return {"supports_snapshots": True, "items": [{
        "id": row.id, "observed_at": _iso(row.observed_at), "source_version": row.source_version,
        "layer_version": row.layer_version, "quality_status": row.quality_status,
    } for row in rows]}


@router.get("/map/tiles/{layer}")
def map_tile_descriptor(
    layer: str,
    response: Response,
    dataset_version_id: Optional[str] = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    descriptor = earth_engine_tiles.describe(db, layer, dataset_version_id)
    response.headers["Cache-Control"] = "private, max-age=300" if descriptor.get("available") else "no-store"
    response.headers["Vary"] = "Accept-Encoding"
    return descriptor


# Map payloads change only through publications and admin approvals, each of
# which writes a PipelineEvent (possibly from the worker process). Keying a
# short in-process cache on the newest event replaces several round trips to
# the remote database with one, without serving data older than a publication.
_MAP_FEATURE_CACHE: Dict[str, tuple] = {}
_MAP_FEATURE_CACHE_SECONDS = 60
_MAP_FEATURE_CACHE_ENTRIES = 32


def _cached_map_features(key: str) -> Optional[tuple]:
    entry = _MAP_FEATURE_CACHE.get(key)
    if entry and entry[0] > time.monotonic():
        return entry
    return None


def _store_map_features(key: str, body: bytes, etag: str) -> None:
    now = time.monotonic()
    for stale in [k for k, v in _MAP_FEATURE_CACHE.items() if v[0] <= now]:
        _MAP_FEATURE_CACHE.pop(stale, None)
    if len(_MAP_FEATURE_CACHE) >= _MAP_FEATURE_CACHE_ENTRIES:
        _MAP_FEATURE_CACHE.pop(min(_MAP_FEATURE_CACHE, key=lambda k: _MAP_FEATURE_CACHE[k][0]), None)
    _MAP_FEATURE_CACHE[key] = (now + _MAP_FEATURE_CACHE_SECONDS, body, etag)


@router.get("/map/features")
def map_features(
    request: Request,
    layer: str = Query(default="priority"),
    bbox: Optional[str] = None,
    limit: int = Query(default=500, ge=1, le=5000),
    layer_version: Optional[int] = Query(default=None, ge=0),
    refresh_version: Optional[int] = Query(default=None, ge=0),
    dataset_version_id: Optional[str] = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
) -> Response:
    known = {item[0]: item for item in LAYER_DEFINITIONS}
    if layer not in known:
        raise HTTPException(status_code=400, detail="Unknown layer")
    cache_headers = {"Cache-Control": "private, max-age=60, stale-while-revalidate=120", "Vary": "Accept-Encoding"}
    generation = db.execute(select(func.count(PipelineEvent.id), func.max(PipelineEvent.created_at))).one()
    cache_key = "|".join(str(item) for item in (
        layer, bbox, limit, layer_version, refresh_version, dataset_version_id, generation[0], generation[1],
    ))
    cached = _cached_map_features(cache_key)
    if cached:
        _, body, etag = cached
        if request.headers.get("If-None-Match") == etag:
            return Response(status_code=304, headers={**cache_headers, "ETag": etag})
        return Response(content=body, media_type="application/json", headers={**cache_headers, "ETag": etag})
    parsed_bbox = _parse_bbox(bbox)
    snapshot = published_version(db, LAYER_DATASETS[layer], dataset_version_id) if layer in LAYER_DATASETS else None
    if dataset_version_id and not snapshot:
        raise HTTPException(status_code=404, detail="Published version is unavailable for this layer")
    features: List[Dict[str, Any]] = []
    if layer == "candidates":
        rows = _viewport_rows(
            db,
            CandidateIntervention,
            parsed_bbox,
            limit,
            CandidateIntervention.active.is_(True),
            defer_geometry=True,
        )
        display = _display_geometries(db, CandidateIntervention, [row.id for row in rows])
        for row in rows:
            features.append(
                {
                    "type": "Feature",
                    "id": row.id,
                    "geometry": display[row.id] if row.id in display else to_geojson(row.geometry),
                    "properties": {
                        "area_id": row.area_id,
                        "name_th": row.name_th,
                        "intervention_type": row.intervention_type,
                        "estimated_cost_thb": row.estimated_cost_thb,
                        "priority_score": row.priority_score,
                        "feasibility_status": row.feasibility_status,
                    },
                }
            )
    else:
        indicator_key = known[layer][2]
        areas_rows = _viewport_rows(db, GeographicArea, parsed_bbox, limit, defer_geometry=True)
        area_ids = [area.id for area in areas_rows]
        display = _display_geometries(db, GeographicArea, area_ids, multi=True)
        score_by_area: Dict[str, PriorityScore] = {}
        observation_by_area: Dict[str, AreaIndicator] = {}
        forecast = None
        projection_by_area: Dict[str, Dict[str, Any]] = {}
        if layer == "priority" and area_ids:
            for score in db.scalars(
                select(PriorityScore)
                .where(PriorityScore.area_id.in_(area_ids))
                .order_by(PriorityScore.area_id, desc(PriorityScore.calculated_at))
            ).all():
                score_by_area.setdefault(score.area_id, score)
        elif layer == "future-risk":
            forecast = db.scalar(select(ForecastRun).order_by(desc(ForecastRun.created_at)).limit(1))
            projections = forecast.results.get("projections", []) if forecast and forecast.results else []
            projection_by_area = {
                str(item["area_id"]): item
                for item in projections
                if isinstance(item, dict) and item.get("area_id") is not None
            }
        elif area_ids:
            observation_query = select(AreaIndicator).where(AreaIndicator.area_id.in_(area_ids), AreaIndicator.indicator_key == indicator_key)
            if snapshot:
                observation_query = observation_query.where(AreaIndicator.dataset_version_id == snapshot.id)
            for observation in db.scalars(
                observation_query.order_by(AreaIndicator.area_id, desc(AreaIndicator.observed_at))
            ).all():
                observation_by_area.setdefault(observation.area_id, observation)
        for area in areas_rows:
            scenario_horizon = None
            scenario_uncertainty = None
            if layer == "priority":
                score = score_by_area.get(area.id)
                value, observed_at, quality = (score.score, score.calculated_at, "derived") if score else (None, None, "unavailable")
            elif layer == "future-risk":
                projection = projection_by_area.get(area.id)
                value, observed_at, quality = (
                    (projection.get("scenario_change_c"), forecast.created_at, "scenario_projection")
                    if projection
                    else (None, None, "unavailable")
                )
                scenario_horizon = forecast.horizon_years if forecast and projection else None
                scenario_uncertainty = projection.get("uncertainty_c") if projection else None
            else:
                observation = observation_by_area.get(area.id)
                value, observed_at, quality = (
                    (observation.value, observation.observed_at, observation.quality_status) if observation else (None, None, "unavailable")
                )
            features.append(
                {
                    "type": "Feature",
                    "id": area.id,
                    "geometry": display[area.id] if area.id in display else to_geojson(area.geometry),
                    "properties": {
                        "area_id": area.id,
                        "name_th": area.name_th,
                        "name_en": area.name_en,
                        "value": value,
                        "unit": known[layer][3],
                        "observed_at": _iso(observed_at),
                        "quality_status": quality,
                        "boundary_quality_status": area.quality_status,
                        "scenario_horizon_years": scenario_horizon,
                        "scenario_uncertainty_c": scenario_uncertainty,
                    },
                }
            )
    payload = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "layer": layer,
            "requested_layer_version": layer_version,
            "requested_refresh_version": refresh_version,
            "dataset_version_id": snapshot.id if snapshot else None,
            "bbox_filter": list(parsed_bbox) if parsed_bbox else None,
            "count": len(features),
            "spatial_filter": "postgis_st_intersects" if parsed_bbox and settings.database_url.startswith("postgresql") else "application_bounds_fallback",
        },
    }
    # Every value is already loaded. Return the pooled connection before the
    # CPU-bound encoding so other requests are not queued behind this one.
    db.close()
    # Encode once: the same bytes are the body and the ETag input, which skips
    # FastAPI's per-coordinate jsonable_encoder walk.
    body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    etag = '"%s"' % hashlib.sha256(body).hexdigest()
    _store_map_features(cache_key, body, etag)
    cache_headers = {**cache_headers, "ETag": etag}
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers=cache_headers)
    return Response(content=body, media_type="application/json", headers=cache_headers)


@router.get("/candidates")
def candidates_list(
    feasibility_status: Optional[str] = None,
    area_id: Optional[str] = None,
    active: bool = True,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    filters = [CandidateIntervention.active.is_(active)]
    if feasibility_status:
        filters.append(CandidateIntervention.feasibility_status == feasibility_status)
    if area_id:
        filters.append(CandidateIntervention.area_id == area_id)
    total = int(
        db.scalar(select(func.count()).select_from(CandidateIntervention).where(*filters)) or 0
    )
    rows = db.scalars(
        select(CandidateIntervention)
        .where(*filters)
        .order_by(desc(CandidateIntervention.priority_score), CandidateIntervention.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_serialize_candidate(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/cost-assumptions")
def cost_assumptions_list(
    intervention_type: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    query = select(CostAssumption)
    if intervention_type:
        query = query.where(CostAssumption.intervention_type == intervention_type)
    rows = db.scalars(
        query.order_by(CostAssumption.intervention_type, desc(CostAssumption.effective_from))
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "intervention_type": row.intervention_type,
                "version": row.version,
                "currency": row.currency,
                "assumptions": row.assumptions,
                "evidence_url": row.evidence_url,
                "effective_from": _iso(row.effective_from),
            }
            for row in rows
        ]
    }


@router.post("/cost-assumptions", status_code=201)
def cost_assumption_create(
    request: CostAssumptionCreateRequest,
    admin_actor: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    duplicate = db.scalar(
        select(CostAssumption).where(
            CostAssumption.intervention_type == request.intervention_type,
            CostAssumption.version == request.version,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Cost assumption version already exists")
    now = datetime.now(timezone.utc)
    row = CostAssumption(
        intervention_type=request.intervention_type,
        version=request.version,
        currency="THB",
        assumptions={
            "status": "approved",
            "cost_basis": request.cost_basis,
            "unit_cost_thb": request.unit_cost_thb,
            "annual_maintenance_thb": request.annual_maintenance_thb,
            "evidence_title": request.evidence_title,
            "notes": request.notes,
            "approved_by": admin_actor,
            "approved_at": now.isoformat(),
        },
        evidence_url=str(request.evidence_url),
        effective_from=_aware(request.effective_from) or now,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Cost assumption version already exists")
    db.add(
        PipelineEvent(
            event_type="cost_assumption.approved",
            layer_key="candidates",
            payload={
                "cost_assumption_id": row.id,
                "intervention_type": row.intervention_type,
                "version": row.version,
                "affected_layers": ["candidates"],
                "approved_by": admin_actor,
            },
        )
    )
    try:
        db.commit()
        _RESPONSE_CACHE.pop("system/data-readiness", None)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Cost assumption version already exists")
    db.refresh(row)
    return {
        "id": row.id,
        "intervention_type": row.intervention_type,
        "version": row.version,
        "currency": row.currency,
        "assumptions": row.assumptions,
        "evidence_url": row.evidence_url,
        "effective_from": _iso(row.effective_from),
    }


@router.post("/candidates/{candidate_id}/approve")
def candidate_approve(
    candidate_id: str,
    request: CandidateApprovalRequest,
    admin_actor: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    candidate = db.get(CandidateIntervention, candidate_id)
    if candidate is None or not candidate.active:
        raise HTTPException(status_code=404, detail="Active candidate not found")
    assumption = db.get(CostAssumption, request.cost_assumption_id)
    if assumption is None or assumption.assumptions.get("status") != "approved":
        raise HTTPException(status_code=422, detail="Approved cost assumption not found")
    now = datetime.now(timezone.utc)
    if _aware(assumption.effective_from) and _aware(assumption.effective_from) > now:
        raise HTTPException(status_code=422, detail="Cost assumption is not effective yet")
    if assumption.intervention_type != candidate.intervention_type:
        raise HTTPException(status_code=422, detail="Cost assumption intervention type does not match candidate")
    if assumption.assumptions.get("cost_basis") != "fixed_per_site":
        raise HTTPException(status_code=422, detail="Candidate requires a fixed_per_site cost assumption")
    unit_cost = assumption.assumptions.get("unit_cost_thb")
    if not isinstance(unit_cost, (int, float)) or unit_cost <= 0:
        raise HTTPException(status_code=422, detail="Cost assumption does not contain a valid unit cost")
    candidate.estimated_cost_thb = float(unit_cost)
    maintenance = assumption.assumptions.get("annual_maintenance_thb")
    candidate.annual_maintenance_thb = float(maintenance) if maintenance is not None else None
    candidate.estimated_beneficiaries = request.estimated_beneficiaries
    candidate.benefit_score = request.benefit_score
    candidate.feasibility_status = "eligible"
    candidate.feasibility_constraints = {
        **(candidate.feasibility_constraints or {}),
        "site_survey_completed": True,
        "land_control_verified": True,
        "design_accessibility_reviewed": True,
        "feasibility_evidence_url": str(request.feasibility_evidence_url),
        "feasibility_notes": request.feasibility_notes,
    }
    candidate.evidence = {
        **(candidate.evidence or {}),
        "cost_assumption_id": assumption.id,
        "cost_evidence": assumption.evidence_url,
        "cost_assumption_version": assumption.version,
        "feasibility_evidence": str(request.feasibility_evidence_url),
        "benefit_evidence": str(request.benefit_evidence_url) if request.benefit_evidence_url else None,
        "approved_at": now.isoformat(),
        "approved_by": admin_actor,
    }
    candidate.uncertainty = {
        **(candidate.uncertainty or {}),
        "cost": "approved versioned fixed-per-site assumption; site-specific variance is not quantified",
        "benefit": "evidence supplied" if request.benefit_evidence_url else "not estimated",
        "feasibility": "administrator-confirmed site evidence supplied",
    }
    candidate.dataset_versions = {
        **(candidate.dataset_versions or {}),
        "cost_assumption": {
            "id": assumption.id,
            "version": assumption.version,
            "effective_from": _iso(assumption.effective_from),
        },
    }
    for run in db.scalars(
        select(OptimizationRun).where(OptimizationRun.is_outdated.is_(False))
    ).all():
        run.is_outdated = True
    db.add(
        PipelineEvent(
            event_type="candidate.approved",
            layer_key="candidates",
            payload={
                "candidate_id": candidate.id,
                "area_id": candidate.area_id,
                "affected_layers": ["candidates"],
                "optimization_inputs_changed": True,
                "approved_by": admin_actor,
            },
        )
    )
    db.commit()
    _RESPONSE_CACHE.pop("system/data-readiness", None)
    db.refresh(candidate)
    return _serialize_candidate(candidate)


@router.get("/priority/rankings")
def priority_rankings(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    latest = select(PriorityScore.area_id, func.max(PriorityScore.calculated_at).label("latest")).group_by(PriorityScore.area_id).subquery()
    total = int(db.scalar(select(func.count()).select_from(latest)) or 0)
    rows = db.execute(
        select(PriorityScore, GeographicArea)
        .join(latest, (PriorityScore.area_id == latest.c.area_id) & (PriorityScore.calculated_at == latest.c.latest))
        .join(GeographicArea, GeographicArea.id == PriorityScore.area_id)
        .order_by(desc(PriorityScore.score))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "rank": (page - 1) * page_size + index + 1,
                "area_id": area.id,
                "area_name_th": area.name_th,
                "score": score.score,
                "components": score.components,
                "missing_inputs": score.missing_inputs,
                "input_versions": score.input_versions,
                "formula_version": score.formula_version,
                "calculated_at": _iso(score.calculated_at),
            }
            for index, (score, area) in enumerate(rows)
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "label": "Heat Exposure Priority",
        "interpretation_warning_th": "คะแนนนี้แสดงลำดับความสำคัญด้านการสัมผัสความร้อน ไม่ใช่ข้อสรุปเรื่องรายได้หรือความเหลื่อมล้ำทางเศรษฐกิจ",
    }


@router.post("/priority/calculate")
def priority_calculate(request: PriorityCalculateRequest) -> Dict[str, Any]:
    components = request.model_dump(exclude={"weights"})
    result = calculate_priority(components, request.weights)
    return {
        "score": result.score,
        "components": result.components,
        "normalized_components": result.normalized_components,
        "effective_weights": result.effective_weights,
        "missing_inputs": result.missing_inputs,
        "formula_version": result.formula_version,
        "sensitivity": sensitivity_range(result),
        "label": "Heat Exposure Priority",
    }


def _candidate_rows(db: Session, ids: Optional[List[str]] = None) -> List[CandidateIntervention]:
    query = select(CandidateIntervention).where(
        CandidateIntervention.active.is_(True),
        CandidateIntervention.estimated_cost_thb.is_not(None),
        CandidateIntervention.feasibility_status == "eligible",
    )
    if ids is not None:
        query = query.where(CandidateIntervention.id.in_(ids))
    return [row for row in db.scalars(query).all() if row.evidence]


def _serialize_candidate(row: CandidateIntervention) -> Dict[str, Any]:
    return {
        "id": row.id,
        "area_id": row.area_id,
        "name_th": row.name_th,
        "intervention_type": row.intervention_type,
        "geometry": to_geojson(row.geometry),
        "estimated_cost_thb": row.estimated_cost_thb,
        "annual_maintenance_thb": row.annual_maintenance_thb,
        "estimated_beneficiaries": row.estimated_beneficiaries,
        "priority_score": row.priority_score,
        "benefit_score": row.benefit_score,
        "feasibility_status": row.feasibility_status,
        "feasibility_constraints": row.feasibility_constraints,
        "evidence": row.evidence,
        "uncertainty": row.uncertainty,
        "dataset_versions": row.dataset_versions,
        "active": row.active,
    }


def _to_optimizer_candidates(rows: List[CandidateIntervention], temperatures=None) -> List[Candidate]:
    return [
        Candidate(
            id=row.id,
            # Reserve whole baht conservatively; never round a site's actual cost down.
            cost_thb=math.ceil(row.estimated_cost_thb or 0),
            beneficiaries=float(row.estimated_beneficiaries or 0),
            priority_score=float(row.priority_score),
            benefit_score=float(row.benefit_score or 0),
            intervention_type=row.intervention_type,
            feasible=row.feasibility_status == "eligible",
            surface_temperature_c=(temperatures or {}).get(row.area_id),
            beneficiaries_available=row.estimated_beneficiaries is not None,
        )
        for row in rows
    ]


def _objective_weights(objective: str, custom: Optional[Dict[str, float]]) -> Dict[str, float]:
    if custom:
        total = sum(custom.values())
        return {key: value / total for key, value in custom.items()}
    return {
        "balanced": {"beneficiaries": 0.35, "priority": 0.30, "benefit": 0.20, "cost_effectiveness": 0.15},
        "beneficiaries": {"beneficiaries": 0.65, "priority": 0.15, "benefit": 0.10, "cost_effectiveness": 0.10},
        "priority": {"beneficiaries": 0.15, "priority": 0.65, "benefit": 0.10, "cost_effectiveness": 0.10},
        "cost_effectiveness": {"beneficiaries": 0.20, "priority": 0.15, "benefit": 0.15, "cost_effectiveness": 0.50},
    }[objective]


def _current_versions(db: Session) -> Dict[str, Any]:
    return current_versions(db)


def _decision_fingerprint(rows, versions, enabled_types):
    allowed = set(enabled_types) if enabled_types is not None else None
    candidates = [_serialize_candidate(row) for row in sorted(rows, key=lambda row: row.id)
                  if allowed is None or row.intervention_type in allowed]
    return hashlib.sha256(json.dumps({"versions": versions, "candidates": candidates},
                                    sort_keys=True, default=str).encode()).hexdigest()


def _serialize_optimization_run(db: Session, run: OptimizationRun) -> Dict[str, Any]:
    allocation_rows = db.scalars(
        select(OptimizationAllocation)
        .where(OptimizationAllocation.run_id == run.id)
        .order_by(OptimizationAllocation.rank)
    ).all()
    selected = []
    estimated_beneficiaries = 0.0
    snapshot_complete = True
    beneficiaries_complete = True
    for allocation in allocation_rows:
        explanation = allocation.explanation or {}
        candidate = explanation.get("candidate_snapshot")
        if not isinstance(candidate, dict):
            # Older runs cannot recover historical geometry/costs by reading today's candidate.
            snapshot_complete = False
            candidate = {"id": allocation.candidate_id, "name_th": "โครงการเดิมที่ไม่มีรายละเอียด snapshot",
                         **{key: explanation.get(key) for key in (
                             "priority_score", "estimated_beneficiaries", "estimated_cost_thb", "evidence", "uncertainty")}}
        beneficiaries_complete = beneficiaries_complete and candidate.get("estimated_beneficiaries") is not None
        estimated_beneficiaries += float(candidate.get("estimated_beneficiaries") or 0)
        selected.append(
            {
                **{key: candidate.get(key) for key in (
                    "id", "area_id", "name_th", "intervention_type", "geometry", "estimated_cost_thb",
                    "annual_maintenance_thb", "estimated_beneficiaries", "priority_score", "feasibility_constraints", "evidence", "uncertainty")},
                "objective_contribution": allocation.objective_contribution,
                "explanation": allocation.explanation,
            }
        )
    return {
        "run_id": run.id,
        "status": run.status,
        "budget_thb": run.budget_thb,
        "allocated_thb": run.allocated_thb,
        "unallocated_thb": max(0, run.budget_thb - run.allocated_thb),
        "estimated_beneficiaries": estimated_beneficiaries if beneficiaries_complete else None,
        "historical_candidate_snapshot_complete": snapshot_complete,
        "objective": run.objective,
        "objective_weights": run.objective_weights,
        "objective_value": run.objective_value,
        "solver": run.solver,
        "selected": selected,
        "input_versions": run.input_versions,
        "is_outdated": run.is_outdated,
        "created_at": _iso(run.created_at),
        "limitations": [
            "ผลประโยชน์เป็นค่าประมาณตามหลักฐานใน candidate แต่ละรายการ",
            "Heat Exposure Priority ไม่ใช่ข้อสรุปเรื่องรายได้หรือความเหลื่อมล้ำทางเศรษฐกิจ",
            "ผลลัพธ์นี้ผูกกับ dataset versions ที่บันทึกไว้และจะไม่ถูกแทนที่โดยอัตโนมัติ",
            "จำนวนผู้รับประโยชน์เป็นผลรวมประมาณการรายโครงการ ยังไม่หักความซ้ำของคนหรือพื้นที่บริการ",
            "ต้นทุนที่มีเศษบาทกันงบโดยปัดขึ้นเป็นบาทเต็ม ไม่ลดราคาจริงเพื่อให้พอดีงบ",
            *([] if snapshot_complete else ["ผลรุ่นเก่าไม่มีรายละเอียด snapshot ครบ แสดงเฉพาะข้อมูลเดิมที่บันทึกไว้ โดยไม่แทนด้วยรายละเอียดโครงการปัจจุบัน"]),
        ],
    }


@router.post("/optimization/run")
def optimization_run(request: OptimizationRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = _candidate_rows(db, request.candidate_ids)
    weights = _objective_weights(request.objective, request.weights)
    portfolio = optimize_portfolio(_to_optimizer_candidates(rows), request.budget_thb, weights, request.enabled_types)
    versions = _current_versions(db)
    run = OptimizationRun(
        budget_thb=request.budget_thb,
        allocated_thb=portfolio.allocated_thb,
        objective=request.objective,
        objective_weights=weights,
        objective_value=portfolio.objective_value,
        solver=portfolio.solver,
        input_versions=versions,
    )
    db.add(run)
    db.flush()
    by_id = {row.id: row for row in rows}
    selected = []
    for rank, candidate_id in enumerate(portfolio.selected_ids, start=1):
        row = by_id[candidate_id]
        explanation = {
            "candidate_snapshot": _serialize_candidate(row),
            "priority_score": row.priority_score,
            "estimated_beneficiaries": row.estimated_beneficiaries,
            "estimated_cost_thb": row.estimated_cost_thb,
            "evidence": row.evidence,
            "uncertainty": row.uncertainty,
        }
        db.add(
            OptimizationAllocation(
                run_id=run.id,
                candidate_id=row.id,
                rank=rank,
                objective_contribution=portfolio.contributions[row.id],
                explanation=explanation,
            )
        )
        selected.append(
            {
                "id": row.id,
                "area_id": row.area_id,
                "name_th": row.name_th,
                "intervention_type": row.intervention_type,
                "geometry": to_geojson(row.geometry),
                "estimated_cost_thb": row.estimated_cost_thb,
                "annual_maintenance_thb": row.annual_maintenance_thb,
                "estimated_beneficiaries": row.estimated_beneficiaries,
                "priority_score": row.priority_score,
                "objective_contribution": portfolio.contributions[row.id],
                "evidence": row.evidence,
                "uncertainty": row.uncertainty,
            }
        )
    db.commit()
    return {
        "run_id": run.id,
        "status": "completed",
        "budget_thb": request.budget_thb,
        "allocated_thb": portfolio.allocated_thb,
        "unallocated_thb": request.budget_thb - portfolio.allocated_thb,
        "estimated_beneficiaries": portfolio.beneficiaries,
        "objective_value": portfolio.objective_value,
        "solver": portfolio.solver,
        "selected": selected,
        "input_versions": versions,
        "limitations": ["ผลประโยชน์เป็นค่าประมาณตามหลักฐานใน candidate แต่ละรายการ", "ไม่ตีความคะแนนความร้อนเป็นฐานะทางเศรษฐกิจ",
                         "ต้นทุนที่มีเศษบาทกันงบโดยปัดขึ้นเป็นบาทเต็ม", *portfolio.limitations],
        "eligible_candidates": len(rows),
        "empty_reason": None if selected else (
            "ยังไม่มีจุดที่ผู้ดูแลอนุมัติหลักฐานรายจุด (สำรวจพื้นที่ สิทธิ์ในที่ดิน และตรวจแบบ) จึงยังไม่มีจุดให้จัดสรรงบจริง "
            "ใช้ \"ประมาณการเบื้องต้น\" เพื่อดูการจัดสรรตามราคาที่อนุมัติแล้ว" if not rows else
            "ไม่มีจุดที่อนุมัติแล้วในประเภทโครงการที่เลือกซึ่งอยู่ในงบนี้"
        ),
    }


PLANNING_ESTIMATE_LIMITATIONS = [
    "ประมาณการเบื้องต้นเพื่อวางแผนเท่านั้น จุดเหล่านี้ยังไม่ผ่านการสำรวจพื้นที่ ยืนยันสิทธิ์ในที่ดิน หรือการตรวจแบบ ห้ามใช้ตัดสินใจลงทุน",
    "ต้นทุนต่อจุดมาจากสมมติฐานราคาที่ผู้ดูแลอนุมัติแล้วของแต่ละประเภทโครงการ ไม่ใช่ราคาของจุดนั้นจริง",
    "ผลนี้ไม่ถูกบันทึกเป็นการตัดสินใจและไม่นับในการตรวจความพร้อมข้อมูล",
    "จุดที่ยังไม่มีประมาณการผู้รับประโยชน์ถูกนับผู้รับประโยชน์เป็นศูนย์ในการจัดลำดับ",
    "ไม่ตีความคะแนนความร้อนเป็นฐานะทางเศรษฐกิจ",
]


def _approved_cost_assumptions(db: Session) -> Dict[str, CostAssumption]:
    """Latest effective approved fixed-per-site assumption for each intervention type."""
    now = datetime.now(timezone.utc)
    latest: Dict[str, CostAssumption] = {}
    for row in db.scalars(select(CostAssumption).order_by(desc(CostAssumption.effective_from))).all():
        terms = row.assumptions or {}
        unit_cost = terms.get("unit_cost_thb")
        if (row.intervention_type in latest or terms.get("status") != "approved"
                or terms.get("cost_basis") != "fixed_per_site"
                or not isinstance(unit_cost, (int, float)) or unit_cost <= 0
                or (_aware(row.effective_from) and _aware(row.effective_from) > now)):
            continue
        latest[row.intervention_type] = row
    return latest


@router.post("/optimization/estimate")
def optimization_estimate(request: OptimizationRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Screening portfolio over unverified candidates priced by approved type-level costs.

    Nothing is persisted and evidence-gated decisions are untouched: this answers
    "what could this budget cover" before any site has been surveyed.
    """
    assumptions = _approved_cost_assumptions(db)
    query = select(CandidateIntervention).where(
        CandidateIntervention.active.is_(True),
        CandidateIntervention.intervention_type.in_(list(assumptions) or [""]),
    )
    if request.candidate_ids is not None:
        query = query.where(CandidateIntervention.id.in_(request.candidate_ids))
    rows = list(db.scalars(query).all())
    weights = _objective_weights(request.objective, request.weights)
    candidates = [
        Candidate(
            id=row.id,
            cost_thb=math.ceil(float(assumptions[row.intervention_type].assumptions["unit_cost_thb"])),
            beneficiaries=float(row.estimated_beneficiaries or 0),
            priority_score=float(row.priority_score or 0),
            benefit_score=float(row.benefit_score or 0),
            intervention_type=row.intervention_type,
            beneficiaries_available=row.estimated_beneficiaries is not None,
        )
        for row in rows
    ]
    portfolio = optimize_portfolio(candidates, request.budget_thb, weights, request.enabled_types)
    by_id = {row.id: row for row in rows}
    selected = []
    for candidate_id in portfolio.selected_ids:
        row = by_id[candidate_id]
        assumption = assumptions[row.intervention_type]
        selected.append({
            "id": row.id,
            "area_id": row.area_id,
            "name_th": row.name_th,
            "intervention_type": row.intervention_type,
            "geometry": to_geojson(row.geometry),
            "estimated_cost_thb": math.ceil(float(assumption.assumptions["unit_cost_thb"])),
            "estimated_beneficiaries": row.estimated_beneficiaries,
            "priority_score": row.priority_score,
            "feasibility_status": row.feasibility_status,
            "objective_contribution": portfolio.contributions[row.id],
            "cost_source": {"cost_assumption_id": assumption.id, "version": assumption.version,
                            "evidence_url": assumption.evidence_url},
        })
    return {
        "run_id": None,
        "mode": "planning_estimate",
        "status": "completed" if assumptions else "no_approved_costs",
        "budget_thb": request.budget_thb,
        "allocated_thb": portfolio.allocated_thb,
        "unallocated_thb": request.budget_thb - portfolio.allocated_thb,
        "estimated_beneficiaries": portfolio.beneficiaries,
        "objective": request.objective,
        "objective_value": portfolio.objective_value,
        "solver": portfolio.solver,
        "priced_types": sorted(assumptions),
        "candidates_considered": len(candidates),
        "selected": selected,
        "limitations": PLANNING_ESTIMATE_LIMITATIONS + (
            [] if assumptions else ["ยังไม่มีสมมติฐานราคาที่อนุมัติแล้ว จึงยังประมาณการไม่ได้"]
        ),
    }


@router.get("/optimization/runs/{run_id}")
def optimization_run_detail(run_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    run = db.get(OptimizationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Optimization run not found")
    return _serialize_optimization_run(db, run)


@router.get("/optimization/runs/{run_id}/report", response_class=HTMLResponse)
def optimization_run_report(run_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    run = db.get(OptimizationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Optimization run not found")
    report = _serialize_optimization_run(db, run)

    def text(value: Any) -> str:
        return html.escape(str(value if value is not None else "—"))

    def json_text(value: Any) -> str:
        return html.escape(json.dumps(value or {}, ensure_ascii=False, indent=2, sort_keys=True))

    project_rows = "".join(
        """
        <tr>
          <td>{rank}</td><td>{name}</td><td>{kind}</td><td class="number">{cost}</td>
          <td class="number">{beneficiaries}</td><td class="number">{priority}</td>
        </tr>
        <tr class="detail"><td></td><td colspan="5"><strong>พื้นที่</strong> {area_id}<br><strong>เรขาคณิต</strong><pre>{geometry}</pre><strong>หลักฐาน</strong><pre>{evidence}</pre><strong>ความไม่แน่นอน</strong><pre>{uncertainty}</pre></td></tr>
        """.format(
            rank=index,
            name=text(item["name_th"]),
            kind=text(item["intervention_type"]),
            cost=_report_number(item["estimated_cost_thb"]),
            beneficiaries=_report_number(item["estimated_beneficiaries"]),
            priority=_report_number(item["priority_score"], 1),
            area_id=text(item["area_id"]),
            geometry=json_text(item["geometry"]),
            evidence=json_text(item["evidence"]),
            uncertainty=json_text(item["uncertainty"]),
        )
        for index, item in enumerate(report["selected"], start=1)
    )
    if not project_rows:
        project_rows = '<tr><td colspan="6">ไม่มีโครงการที่ผ่านข้อจำกัดภายใต้งบประมาณนี้</td></tr>'
    outdated_notice = (
        '<div class="warning"><strong>ผลลัพธ์อาจล้าสมัย</strong> มี dataset version ใหม่กว่าข้อมูลที่ใช้คำนวณ โปรดรัน optimizer อีกครั้งก่อนตัดสินใจ</div>'
        if report["is_outdated"]
        else '<div class="ok"><strong>เวอร์ชันข้อมูลยังตรงกัน</strong> ณ เวลาที่ส่งออกรายงาน</div>'
    )
    limitations = "".join("<li>%s</li>" % text(item) for item in report["limitations"])
    content = """<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HEAT//SHIFT Decision Report {run_id}</title>
<style>
  :root {{ color-scheme: light; font-family: system-ui, -apple-system, "Noto Sans Thai", sans-serif; color: #14212b; }}
  body {{ margin: 0 auto; max-width: 1080px; padding: 40px; background: #f4f7f8; }}
  header {{ padding: 28px; color: white; background: #0a1822; border-top: 5px solid #ff5a36; }}
  h1 {{ margin: 4px 0; font-size: 30px; }} h2 {{ margin-top: 32px; }} .tag {{ color: #4eeaff; letter-spacing: .14em; }}
  .meta, table {{ width: 100%; border-collapse: collapse; background: white; }}
  .meta td, th, td {{ padding: 11px 12px; border: 1px solid #dbe3e6; vertical-align: top; }}
  th {{ text-align: left; background: #eaf0f2; }} .number {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .warning, .ok {{ margin: 20px 0; padding: 14px 16px; border-radius: 6px; }}
  .warning {{ background: #fff1e8; border: 1px solid #f59b68; }} .ok {{ background: #e9f8f1; border: 1px solid #68c99d; }}
  pre {{ white-space: pre-wrap; word-break: break-word; margin: 5px 0 10px; color: #40515c; }}
  .detail td {{ background: #f8fafb; font-size: 12px; }} footer {{ margin-top: 36px; color: #5f707a; font-size: 12px; }}
  @media print {{ body {{ padding: 0; background: white; }} .detail {{ break-inside: avoid; }} }}
</style></head><body>
<header><div class="tag">HEAT//SHIFT · DECISION REPORT</div><h1>รายงานพอร์ตการลงทุนลดความร้อน</h1><div>Don't Just Map the Heat. Change the City.</div></header>
{outdated_notice}
<table class="meta">
  <tr><td><strong>Run ID</strong><br>{run_id}</td><td><strong>สร้างเมื่อ</strong><br>{created_at}</td><td><strong>Solver</strong><br>{solver}</td></tr>
  <tr><td><strong>งบประมาณ</strong><br>฿{budget:,.0f}</td><td><strong>จัดสรรแล้ว</strong><br>฿{allocated:,.0f}</td><td><strong>ผลรวมประมาณการรายโครงการ (ยังไม่หักซ้ำ)</strong><br>{beneficiaries}</td></tr>
  <tr><td><strong>Objective</strong><br>{objective}</td><td><strong>Objective value</strong><br>{objective_value}</td><td><strong>จำนวนโครงการ</strong><br>{project_count}</td></tr>
</table>
<h2>โครงการที่ได้รับเลือก</h2>
<table><thead><tr><th>#</th><th>โครงการ</th><th>ประเภท</th><th>ต้นทุน (THB)</th><th>ผู้รับประโยชน์</th><th>Priority</th></tr></thead><tbody>{project_rows}</tbody></table>
<h2>เวอร์ชันข้อมูลที่ใช้</h2><pre>{versions}</pre>
<h2>น้ำหนัก objective</h2><pre>{weights}</pre>
<h2>ข้อจำกัดในการตีความ</h2><ul>{limitations}</ul>
<footer>สร้างโดย HEAT//SHIFT จากผลลัพธ์ backend ที่บันทึกไว้ รายงานนี้ไม่ใช่การรับรองราคาจัดซื้อหรือผลการลดอุณหภูมิจริง</footer>
</body></html>""".format(
        run_id=text(report["run_id"]),
        created_at=text(report["created_at"]),
        solver=text(report["solver"]),
        budget=float(report["budget_thb"] or 0),
        allocated=float(report["allocated_thb"] or 0),
        beneficiaries=_report_number(report["estimated_beneficiaries"]),
        objective=text(report["objective"]),
        objective_value=text(report["objective_value"]),
        project_count=len(report["selected"]),
        project_rows=project_rows,
        versions=json_text(report["input_versions"]),
        weights=json_text(report["objective_weights"]),
        limitations=limitations,
        outdated_notice=outdated_notice,
    )
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="heat-shift-decision-report-%s.html"' % run.id,
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'",
        },
    )


@router.post("/scenarios/compare")
def scenarios_compare(request: ScenarioCompareRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    versions = _current_versions(db)
    candidate_rows = _candidate_rows(db, request.candidate_ids)
    temperature_version = versions.get(LAYER_DATASETS["temperature"], {}).get("version_id")
    temperatures = dict(db.execute(select(AreaIndicator.area_id, AreaIndicator.value).where(
        AreaIndicator.dataset_version_id == temperature_version, AreaIndicator.indicator_key == "lst_c_mean",
        AreaIndicator.area_id.in_([row.area_id for row in candidate_rows]),
    )).all()) if temperature_version else {}
    candidates = _to_optimizer_candidates(candidate_rows, temperatures)
    candidates_by_id = {candidate.id: candidate for candidate in candidate_rows}
    common_weights = _objective_weights("balanced", None)
    definitions = [
        ("hottest_first", "ร้อนที่สุดก่อน"),
        ("population_first", "ประชากรก่อน"),
        ("heatshift_optimized", "HEAT//SHIFT สมดุล"),
    ]
    results = []
    for key, label in definitions:
        portfolio = (optimize_portfolio(candidates, request.budget_thb, common_weights, request.enabled_types)
                     if key == "heatshift_optimized" else
                     baseline_portfolio(candidates, request.budget_thb, key, common_weights, request.enabled_types))
        allocations = []
        for rank, candidate_id in enumerate(portfolio.selected_ids, start=1):
            candidate = candidates_by_id[candidate_id]
            allocations.append(
                {
                    "rank": rank,
                    "id": candidate.id,
                    "area_id": candidate.area_id,
                    "name_th": candidate.name_th,
                    "intervention_type": candidate.intervention_type,
                    "geometry": to_geojson(candidate.geometry),
                    "estimated_cost_thb": candidate.estimated_cost_thb,
                    "annual_maintenance_thb": candidate.annual_maintenance_thb,
                    "estimated_beneficiaries": candidate.estimated_beneficiaries,
                    "priority_score": candidate.priority_score,
                    "objective_contribution": portfolio.contributions[candidate.id],
                    "evidence": candidate.evidence,
                    "uncertainty": candidate.uncertainty,
                }
            )
        results.append(
            {
                "strategy": key,
                "label_th": label,
                "selected_ids": portfolio.selected_ids,
                "allocations": allocations,
                "allocated_thb": portfolio.allocated_thb,
                "unallocated_thb": request.budget_thb - portfolio.allocated_thb,
                "estimated_beneficiaries": portfolio.beneficiaries,
                "covered_area_ids": sorted({item["area_id"] for item in allocations}),
                "objective_value": portfolio.objective_value,
                "solver": portfolio.solver,
                "weights": common_weights,
                "status": portfolio.status,
                "limitations": portfolio.limitations,
            }
        )
    if _current_versions(db) != versions:
        raise HTTPException(409, "ข้อมูลเปลี่ยนระหว่างเปรียบเทียบ กรุณาคำนวณใหม่เพื่อใช้เวอร์ชันเดียวกัน")
    fingerprint = _decision_fingerprint(candidate_rows, versions, request.enabled_types)
    return {
        "budget_thb": request.budget_thb,
        "scenarios": results,
        "input_versions": versions,
        "input_fingerprint": fingerprint,
        "eligible_candidate_ids": [c.id for c in eligible_candidates(candidates, request.enabled_types)],
        "eligibility_consistent": True,
        "comparison_note_th": "ใช้โครงการที่มีสิทธิ์ เวอร์ชันข้อมูล และงบชุดเดียวกัน ร้อนที่สุดก่อน/ประชากรก่อนเรียงตามหลักฐานแล้วเลือกที่งบพอ ส่วน HEAT//SHIFT ใช้ OR-Tools คะแนน objective ประเมินทุกพอร์ตด้วยน้ำหนักสมดุลเดียวกัน หากหลักฐานจัดอันดับขาดจะแสดงข้อมูลไม่เพียงพอ",
    }


def _serialize_saved_scenario(
    db: Session,
    scenario: SavedScenario,
    current_versions: Optional[Dict[str, Any]] = None,
    include_comparison: bool = True,
) -> Dict[str, Any]:
    configuration = scenario.configuration if isinstance(scenario.configuration, dict) else {}
    comparison = configuration.get("comparison", {})
    if not isinstance(comparison, dict):
        comparison = {}
    versions = current_versions if current_versions is not None else _current_versions(db)
    fingerprint = comparison.get("input_fingerprint")
    candidate_inputs_changed = bool(fingerprint) and fingerprint != _decision_fingerprint(
        _candidate_rows(db, configuration.get("candidate_ids")), versions, configuration.get("enabled_types"))
    return {
        "id": scenario.id,
        "name": scenario.name,
        "budget_thb": configuration.get("budget_thb"),
        "enabled_types": configuration.get("enabled_types"),
        "candidate_ids": configuration.get("candidate_ids"),
        "comparison": comparison if include_comparison else {},
        "dataset_versions": scenario.dataset_versions,
        "is_outdated": scenario.dataset_versions != versions or candidate_inputs_changed,
        "created_at": _iso(scenario.created_at),
    }


@router.post("/scenarios/save", status_code=201)
def scenario_save(request: ScenarioSaveRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    comparison = scenarios_compare(
        ScenarioCompareRequest(
            budget_thb=request.budget_thb,
            enabled_types=request.enabled_types,
            candidate_ids=request.candidate_ids,
        ),
        db,
    )
    scenario = SavedScenario(
        name=request.name.strip(),
        configuration={
            "budget_thb": request.budget_thb,
            "enabled_types": request.enabled_types,
            "candidate_ids": request.candidate_ids,
            "comparison": comparison,
        },
        dataset_versions=comparison["input_versions"],
    )
    db.add(scenario)
    db.commit()
    return _serialize_saved_scenario(db, scenario)


@router.get("/scenarios")
def saved_scenarios(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    total = int(db.scalar(select(func.count()).select_from(SavedScenario)) or 0)
    rows = db.scalars(
        select(SavedScenario).order_by(desc(SavedScenario.created_at)).offset((page - 1) * page_size).limit(page_size)
    ).all()
    current_versions = _current_versions(db)
    return {
        "items": [_serialize_saved_scenario(db, row, current_versions, include_comparison=False) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/scenarios/{scenario_id}")
def saved_scenario_detail(scenario_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    scenario = db.get(SavedScenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Saved scenario not found")
    return _serialize_saved_scenario(db, scenario)


@router.get("/scenarios/{scenario_id}/report", response_class=HTMLResponse)
def saved_scenario_report(scenario_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    scenario = db.get(SavedScenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Saved scenario not found")
    report = _serialize_saved_scenario(db, scenario)
    comparison = report["comparison"]

    def text(value: Any) -> str:
        return html.escape(str(value if value is not None else "—"))

    def json_text(value: Any) -> str:
        return html.escape(json.dumps(value or {}, ensure_ascii=False, indent=2, sort_keys=True))

    sections = []
    for result in comparison.get("scenarios", []):
        rows = "".join(
            "<tr><td>{rank}</td><td>{name}</td><td>{kind}</td><td class=\"number\">{cost}</td>"
            "<td class=\"number\">{beneficiaries}</td><td><pre>{evidence}</pre></td></tr>"
            "<tr class=\"detail\"><td></td><td colspan=\"5\"><strong>พื้นที่</strong> {area_id}<br>"
            "<strong>เรขาคณิต</strong><pre>{geometry}</pre><strong>ความไม่แน่นอน</strong><pre>{uncertainty}</pre></td></tr>".format(
                rank=int(item.get("rank", index)),
                name=text(item.get("name_th")),
                kind=text(item.get("intervention_type")),
                cost=_report_number(item.get("estimated_cost_thb")),
                beneficiaries=_report_number(item.get("estimated_beneficiaries")),
                evidence=json_text(item.get("evidence")),
                area_id=text(item.get("area_id")),
                geometry=json_text(item.get("geometry")),
                uncertainty=json_text(item.get("uncertainty")),
            )
            for index, item in enumerate(result.get("allocations", []), start=1)
        )
        if not rows:
            rows = '<tr><td colspan="6">ไม่มีโครงการที่ผ่านข้อจำกัด</td></tr>'
        sections.append(
            """
            <section><h2>{label}</h2>
            <div class="summary">ใช้งบ ฿{allocated:,.0f} · ผลรวมประมาณการรายโครงการ (ยังไม่หักซ้ำ) {beneficiaries} คน · Objective {objective} · ครอบคลุม {covered}</div>
            <p>{limitations}</p>
            <table><thead><tr><th>#</th><th>โครงการ</th><th>ประเภท</th><th>ต้นทุน (THB)</th><th>ผู้รับประโยชน์</th><th>หลักฐาน</th></tr></thead><tbody>{rows}</tbody></table>
            </section>
            """.format(
                label=text(result.get("label_th") or result.get("strategy")),
                allocated=float(result.get("allocated_thb") or 0),
                beneficiaries=_report_number(result.get("estimated_beneficiaries")),
                limitations=text(" · ".join(result.get("limitations", []))),
                objective=text(result.get("objective_value")),
                covered=text(", ".join(result.get("covered_area_ids", [])) or "—"),
                rows=rows,
            )
        )
    outdated_notice = (
        '<div class="warning"><strong>ผลเปรียบเทียบอาจล้าสมัย</strong> มี dataset version ใหม่กว่า โปรดคำนวณและบันทึกฉากทัศน์ใหม่ก่อนตัดสินใจ</div>'
        if report["is_outdated"]
        else '<div class="ok"><strong>เวอร์ชันข้อมูลยังตรงกัน</strong> ณ เวลาที่ส่งออกรายงาน</div>'
    )
    content = """<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HEAT//SHIFT Scenario Report {scenario_id}</title>
<style>
  :root {{ color-scheme: light; font-family: system-ui, -apple-system, "Noto Sans Thai", sans-serif; color: #14212b; }}
  body {{ margin: 0 auto; max-width: 1080px; padding: 40px; background: #f4f7f8; }}
  header {{ padding: 28px; color: white; background: #0a1822; border-top: 5px solid #4eeaff; }}
  h1 {{ margin: 4px 0; font-size: 28px; }} h2 {{ margin-top: 30px; }} .tag {{ color: #4eeaff; letter-spacing: .14em; }}
  .meta, table {{ width: 100%; border-collapse: collapse; background: white; }}
  .meta td, th, td {{ padding: 10px 11px; border: 1px solid #dbe3e6; vertical-align: top; }}
  th {{ text-align: left; background: #eaf0f2; }} .number {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .summary {{ margin: -12px 0 10px; color: #52646f; }} pre {{ white-space: pre-wrap; word-break: break-word; margin: 0; font-size: 11px; }}
  .detail td {{ background: #f8fafb; font-size: 12px; }}
  .warning, .ok {{ margin: 20px 0; padding: 14px 16px; border-radius: 6px; }}
  .warning {{ background: #fff1e8; border: 1px solid #f59b68; }} .ok {{ background: #e9f8f1; border: 1px solid #68c99d; }}
  footer {{ margin-top: 36px; color: #5f707a; font-size: 12px; }} @media print {{ body {{ padding: 0; background: white; }} section {{ break-inside: avoid; }} }}
</style></head><body>
<header><div class="tag">HEAT//SHIFT · SCENARIO REPORT</div><h1>{name}</h1><div>เปรียบเทียบยุทธศาสตร์ภายใต้ candidate, feasibility และเพดานงบเดียวกัน</div></header>
{outdated_notice}
<table class="meta"><tr><td><strong>Scenario ID</strong><br>{scenario_id}</td><td><strong>บันทึกเมื่อ</strong><br>{created_at}</td><td><strong>งบประมาณ</strong><br>฿{budget:,.0f}</td></tr></table>
{sections}
<h2>เวอร์ชันข้อมูลที่ใช้</h2><pre>{versions}</pre>
<h2>เงื่อนไขที่บันทึก</h2><pre>{configuration}</pre>
<footer>ผลประโยชน์เป็นค่าประมาณตามหลักฐานของ candidate รายงานนี้ไม่ใช่การรับรองราคาจัดซื้อหรือผลการลดอุณหภูมิจริง</footer>
</body></html>""".format(
        name=text(report["name"]),
        scenario_id=text(report["id"]),
        created_at=text(report["created_at"]),
        budget=float(report["budget_thb"] or 0),
        sections="".join(sections),
        versions=json_text(report["dataset_versions"]),
        configuration=json_text(
            {
                "enabled_types": report["enabled_types"],
                "candidate_ids": report["candidate_ids"],
                "eligibility_consistent": comparison.get("eligibility_consistent"),
                "comparison_note_th": comparison.get("comparison_note_th"),
            }
        ),
        outdated_notice=outdated_notice,
    )
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="heat-shift-scenario-%s.html"' % scenario.id,
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'",
        },
    )


@router.post("/forecast/run")
def forecast_run(request: ForecastRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        run, reused = compute_forecast(db, request.horizon_years, request.area_ids)
    except ForecastInProgress:
        raise HTTPException(status_code=409, detail="กำลังคำนวณฉากทัศน์จากข้อมูลชุดนี้ กรุณาลองอีกครั้ง")
    except LockBackendUnavailable:
        raise HTTPException(status_code=503, detail="ระบบคิวและล็อกการคำนวณยังไม่พร้อม")
    return {
        "run_id": run.id,
        **run.results,
        "cached": reused,
        "method": run.method,
        "model_version": run.model_version,
        "training_versions": run.training_versions,
        "assumptions": run.assumptions,
        "metrics": run.metrics,
        "created_at": _iso(run.created_at),
    }


def _budget_from_question(question: str) -> Optional[int]:
    normalized = question.translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")).replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(ล้าน(?:บาท)?|บาท)", normalized)
    if not match:
        return None
    value = float(match.group(1))
    if match.group(2).startswith("ล้าน"):
        value *= 1_000_000
    return int(round(value))


_THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
_THAI_STATUS = {
    "up_to_date": "เป็นปัจจุบัน",
    "updating": "กำลังอัปเดต",
    "stale": "ข้อมูลเก่า",
    "unavailable": "ไม่พร้อมใช้งาน",
    "error": "เกิดข้อผิดพลาด",
}
_THAI_COMPONENTS = {
    "temperature_anomaly": "ความร้อนผิดปกติ",
    "population_exposure": "ประชากรสัมผัส",
    "vegetation_deficit": "การขาดพืชพรรณ",
    "built_intensity": "ความหนาแน่นสิ่งปลูกสร้าง",
    "historical_change": "การเปลี่ยนแปลงย้อนหลัง",
}


def _thai_datetime(value: Any) -> str:
    """Bangkok-time Thai date for assistant prose, e.g. 9 พ.ย. 2569 22:00 น."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return "ไม่ทราบเวลา"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(timezone(timedelta(hours=7)))
    return "%d %s %d %02d:%02d น." % (local.day, _THAI_MONTHS[local.month - 1], local.year + 543, local.hour, local.minute)


@router.post("/assistant/query")
def assistant_query(request: AssistantQuery, db: Session = Depends(get_db)) -> Dict[str, Any]:
    question = request.question.lower()
    grounding: List[Dict[str, Any]] = []
    optimization: Optional[Dict[str, Any]] = None
    parsed_budget = request.budget_thb if request.budget_thb is not None else _budget_from_question(question)
    budget_mentioned = request.budget_thb is not None or parsed_budget is not None or "งบ" in question
    if budget_mentioned:
        if parsed_budget is None:
            answer = "กรุณาระบุงบประมาณเป็นบาทหรือหน่วยล้านบาท เช่น 5 ล้านบาท เพื่อให้ระบบคำนวณจาก candidate ที่มีหลักฐาน"
        elif not 100_000 <= parsed_budget <= 100_000_000:
            answer = "City Optimizer รองรับงบประมาณตั้งแต่ 100,000 ถึง 100,000,000 บาท"
        else:
            candidates = [candidate for candidate in _candidate_rows(db) if candidate.evidence]
            if not candidates:
                answer = "ยังไม่มี candidate intervention ที่มีหลักฐานในฐานข้อมูล จึงไม่ควรสร้างตำแหน่งลงทุนขึ้นเอง"
            else:
                optimization = optimization_run(
                    OptimizationRequest(
                        budget_thb=parsed_budget,
                        objective="balanced",
                        candidate_ids=[candidate.id for candidate in candidates],
                    ),
                    db,
                )
                selected = optimization["selected"]
                if not selected:
                    answer = "ยังไม่มี candidate ที่ feasible และมีต้นทุนอยู่ภายในงบ %s บาท" % f"{parsed_budget:,.0f}"
                else:
                    preview = ", ".join(item["name_th"] for item in selected[:3])
                    if len(selected) > 3:
                        preview += " และอีก %s โครงการ" % (len(selected) - 3)
                    answer = (
                        "จาก candidate ที่มีหลักฐาน ระบบ OR-Tools เลือก %s โครงการ ใช้งบ %s จาก %s บาท "
                        "ผู้รับประโยชน์โดยประมาณ %s คน ได้แก่ %s ผลลัพธ์นี้ผูกกับ dataset versions ปัจจุบันและควรคำนวณใหม่เมื่อข้อมูลเปลี่ยน"
                        % (
                            len(selected),
                            f"{optimization['allocated_thb']:,.0f}",
                            f"{parsed_budget:,.0f}",
                            ("ยังไม่มีหลักฐานครบ" if optimization['estimated_beneficiaries'] is None
                             else f"{optimization['estimated_beneficiaries']:,.0f} (ผลรวมรายโครงการ ยังไม่หักซ้ำ)"),
                            preview,
                        )
                    )
                grounding.append(
                    {
                        "type": "optimization_run",
                        "id": optimization["run_id"],
                        "input_versions": optimization["input_versions"],
                        "candidate_ids": [item["id"] for item in selected],
                    }
                )
    elif "ล่าสุด" in question or "ข้อมูลใหม่" in question or "ดาวเทียม" in question:
        sources = _source_statuses(db, list(db.scalars(select(DataSource)).all()))
        observed = [item for item in sources if item["last_source_observation"]]
        if not observed:
            answer = "ขณะนี้ยังไม่มีข้อมูลที่ผ่านการตรวจสอบและเผยแพร่ จึงไม่ควรระบุวันที่สังเกตการณ์"
        else:
            latest = max(observed, key=lambda item: item["last_source_observation"])
            answer = "ข้อมูลที่มีเวลาสังเกตการณ์ล่าสุดมาจาก %s เมื่อ %s สถานะ%s" % (
                latest["source_name"], _thai_datetime(latest["last_source_observation"]),
                _THAI_STATUS.get(latest["status"], latest["status"]),
            )
            grounding.append({"type": "dataset", "id": latest["id"], "layer_version": latest["layer_version"]})
    elif any(token in question for token in ["ทำไม", "คะแนน", "พื้นที่นี้"]):
        if not request.area_id:
            answer = "กรุณาเลือกพื้นที่บนแผนที่ก่อน เพื่ออธิบายจากข้อมูลของพื้นที่นั้นโดยตรง"
        else:
            area = db.get(GeographicArea, request.area_id)
            score = db.scalar(select(PriorityScore).where(PriorityScore.area_id == request.area_id).order_by(desc(PriorityScore.calculated_at)).limit(1))
            if not area or not score:
                answer = "พื้นที่ที่เลือกยังไม่มีคะแนน Heat Exposure Priority ที่ผ่านการคำนวณ"
            else:
                component_text = ", ".join(
                    "%s %s" % (_THAI_COMPONENTS.get(key, key), f"{value:,.2f}" if isinstance(value, (int, float)) else value)
                    for key, value in score.components.items() if value is not None
                )
                answer = "%s มีคะแนน Heat Exposure Priority %.1f จากองค์ประกอบ %s ไม่ใช่ข้อสรุปเรื่องรายได้หรือความเหลื่อมล้ำทางเศรษฐกิจ" % (
                    area.name_th, score.score, component_text or "ข้อมูลที่มีอยู่"
                )
                grounding.append({"type": "priority_score", "id": score.id, "formula_version": score.formula_version})
    else:
        top = db.execute(
            select(PriorityScore, GeographicArea)
            .join(GeographicArea, GeographicArea.id == PriorityScore.area_id)
            .order_by(desc(PriorityScore.score))
            .limit(1)
        ).first()
        if not top:
            answer = "ยังไม่มีคะแนน Heat Exposure Priority ที่ผ่านการคำนวณ จึงไม่สามารถระบุพื้นที่เสี่ยงสูงสุดได้"
        else:
            score, area = top
            answer = "จากคะแนนที่มีอยู่ %s มี Heat Exposure Priority สูงสุดที่ %.1f (คำนวณเมื่อ %s)" % (
                area.name_th, score.score, _thai_datetime(score.calculated_at)
            )
            grounding.append({"type": "priority_score", "id": score.id, "area_id": area.id})
    return {
        "answer_th": answer,
        "grounding": grounding,
        "optimization": optimization,
        "mode": "deterministic",
        "invented_values": False,
    }


def _connector_for(source: str, fixture_path: Optional[str] = None):
    if source == "bma-boundaries":
        return BangkokBoundaryConnector()
    if source == "landsat":
        return LandsatConnector()
    if source == "sentinel":
        return SentinelConnector()
    if source == "dynamic-world":
        return DynamicWorldConnector()
    if source == "open-meteo":
        return OpenMeteoConnector()
    if source == "worldpop":
        return WorldPopConnector()
    if source == "osm":
        return OpenStreetMapConnector()
    if source == "fixture":
        return FixtureConnector(Path(fixture_path or ""))
    raise ValueError("Unsupported source")


def _run_manual_job(job_id: str, source: str, fixture_path: Optional[str]) -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        job = db.get(IngestionJob, job_id)
        if job is None or job.status == "completed":
            return
        job.status, job.stage, job.started_at, job.progress = "running", "source_check", datetime.now(timezone.utc), 0.1
        job.attempts += 1
        db.commit()
        result = run_monitor(db, _connector_for(source, fixture_path), requested_by="admin")
        if result.get("status") == "locked":
            raise RuntimeError("Another worker is processing this source; try again after it completes")
        job = db.get(IngestionJob, job_id)
        job.status, job.stage, job.finished_at, job.progress = "completed", "completed", datetime.now(timezone.utc), 1.0
        job.checkpoint = result
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(IngestionJob, job_id)
        if job:
            job.status, job.stage, job.finished_at, job.error_message = "failed", "failed", datetime.now(timezone.utc), str(exc)
            db.commit()
    finally:
        db.close()


@router.get("/ingestion/jobs")
def ingestion_jobs(
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    query = select(IngestionJob)
    if status:
        query = query.where(IngestionJob.status == status)
    rows = db.scalars(query.order_by(desc(IngestionJob.created_at)).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "items": [
            {
                "id": row.id,
                "source_id": row.source_id,
                "dataset_version_id": row.dataset_version_id,
                "job_type": row.job_type,
                "status": row.status,
                "stage": row.stage,
                "progress": row.progress,
                "attempts": row.attempts,
                "checkpoint": row.checkpoint,
                "created_at": _iso(row.created_at),
                "started_at": _iso(row.started_at),
                "finished_at": _iso(row.finished_at),
                "error_message": _ingestion_error_summary(row.error_message),
            }
            for row in rows
        ]
    }


@router.get("/ingestion/status")
def ingestion_status(job_id: Optional[str] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    raw_counts = dict(db.execute(select(IngestionJob.status, func.count()).group_by(IngestionJob.status)).all())
    counts = {status: int(raw_counts.get(status, 0)) for status in ["queued", "running", "completed", "failed"]}
    last_event = db.scalar(select(PipelineEvent).order_by(desc(PipelineEvent.created_at)).limit(1))
    job = db.get(IngestionJob, job_id) if job_id else None
    recent_jobs = db.scalars(select(IngestionJob).order_by(desc(IngestionJob.created_at)).limit(20)).all()
    recent_events = db.scalars(select(PipelineEvent).order_by(desc(PipelineEvent.created_at)).limit(30)).all()
    sources = db.scalars(
        select(DataSource).where(DataSource.source_type != "test_fixture").order_by(DataSource.name)
    ).all()
    source_checks = []
    latest_checks = _latest_source_rows(db, SourceCheck, [source.id for source in sources], SourceCheck.checked_at)
    for source in sources:
        check = latest_checks.get(source.id)
        source_checks.append(
            {
                "source_id": source.id,
                "source_name": source.name,
                "dataset_id": source.dataset_id,
                "status": check.status if check else "never_checked",
                "checked_at": _iso(check.checked_at) if check else None,
                "completed_at": _iso(check.completed_at) if check else None,
                "discovered_count": check.discovered_count if check else 0,
                "processed_count": check.queued_count if check else 0,
                "provider_quota": check.provider_quota if check and check.provider_quota else {"status": "not_reported"},
                "error_message": _ingestion_error_summary(check.error_message) if check else None,
            }
        )
    area_count = int(db.scalar(select(func.count()).select_from(GeographicArea)) or 0)
    covered_area_count = int(db.scalar(select(func.count(func.distinct(AreaIndicator.area_id)))) or 0)
    return {
        "queue": counts,
        "summary": {
            "discovered_assets_latest_checks": sum(item["discovered_count"] for item in source_checks),
            "processed_assets_latest_checks": sum(item["processed_count"] for item in source_checks),
            "failed_jobs_total": counts["failed"],
            "sources_with_errors": sum(1 for item in source_checks if item["status"] in {"failed", "degraded"}),
        },
        "coverage": {
            "geographic_areas": area_count,
            "areas_with_indicators": covered_area_count,
            "percent": round((covered_area_count / area_count) * 100, 1) if area_count else 0.0,
        },
        "source_checks": source_checks,
        "recent_jobs": [
            {
                "id": item.id,
                "source_id": item.source_id,
                "job_type": item.job_type,
                "status": item.status,
                "stage": item.stage,
                "progress": item.progress,
                "attempts": item.attempts,
                "requested_by": item.requested_by,
                "created_at": _iso(item.created_at),
                "finished_at": _iso(item.finished_at),
                "error_message": _ingestion_error_summary(item.error_message),
            }
            for item in recent_jobs
        ],
        "recent_events": [
            {
                "id": item.id,
                "type": item.event_type,
                "source_id": item.source_id,
                "dataset_version_id": item.dataset_version_id,
                "layer": item.layer_key,
                "layer_version": item.layer_version,
                "payload": item.payload,
                "created_at": _iso(item.created_at),
            }
            for item in recent_events
        ],
        "job": None
        if not job
        else {
            "id": job.id,
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "checkpoint": job.checkpoint,
            "error_message": _ingestion_error_summary(job.error_message),
            "finished_at": _iso(job.finished_at),
        },
        "last_event": None
        if not last_event
        else {"type": last_event.event_type, "layer": last_event.layer_key, "layer_version": last_event.layer_version, "created_at": _iso(last_event.created_at)},
    }


@router.post("/ingestion/run", status_code=202)
def ingestion_run(
    request: IngestionRequest,
    background_tasks: BackgroundTasks,
    admin_actor: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    fixture_path = request.fixture_path
    if request.source == "fixture":
        if not settings.allow_fixture_ingestion:
            raise HTTPException(status_code=403, detail="Fixture ingestion is disabled")
        if not fixture_path:
            raise HTTPException(status_code=422, detail="fixture_path is required")
        root = settings.fixture_root.resolve()
        path = Path(fixture_path).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(status_code=400, detail="fixture_path must be an existing file below FIXTURE_ROOT")
        fixture_path = str(path)
    job = IngestionJob(
        job_type="manual_refresh",
        status="queued",
        stage="queued",
        requested_by="admin:%s" % admin_actor,
    )
    db.add(job)
    db.commit()
    dispatch_mode = "api-background-fallback"
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
        client.ping()
        client.close()
        from app.worker import run_manual_ingestion

        run_manual_ingestion.delay(job.id, request.source, fixture_path)
        dispatch_mode = "celery-durable-queue"
    except Exception:
        if settings.environment.lower() == "production":
            job.status = "failed"
            job.stage = "dispatch_failed"
            job.error_message = "Durable queue is unavailable; job was not executed"
            db.commit()
            raise HTTPException(status_code=503, detail="Durable ingestion queue is unavailable")
        background_tasks.add_task(_run_manual_job, job.id, request.source, fixture_path)
    return {"job_id": job.id, "status": "queued", "status_url": "/api/ingestion/status?job_id=%s" % job.id, "dispatch_mode": dispatch_mode}


def _events_after_cursor(db: Session, cursor: str, limit: int = 50) -> Optional[List[PipelineEvent]]:
    cursor_event = db.get(PipelineEvent, cursor)
    if cursor_event is None:
        return None
    return list(
        db.scalars(
            select(PipelineEvent)
            .where(
                or_(
                    PipelineEvent.created_at > cursor_event.created_at,
                    and_(PipelineEvent.created_at == cursor_event.created_at, PipelineEvent.id > cursor_event.id),
                )
            )
            .order_by(PipelineEvent.created_at, PipelineEvent.id)
            .limit(limit)
        ).all()
    )


@router.get("/events/stream")
async def events_stream(request: Request, after: Optional[str] = None, db: Session = Depends(get_db)) -> StreamingResponse:
    def initial_cursor(cursor):
        try:
            if cursor and db.get(PipelineEvent, cursor) is None:
                cursor = None
            if cursor is None:
                latest = db.scalar(
                    select(PipelineEvent).order_by(desc(PipelineEvent.created_at), desc(PipelineEvent.id)).limit(1)
                )
                cursor = latest.id if latest else None
            return cursor
        finally:
            db.rollback()

    def read_frames(cursor):
        try:
            if cursor:
                rows = _events_after_cursor(db, cursor)
                if rows is None:
                    latest = db.scalar(
                        select(PipelineEvent).order_by(desc(PipelineEvent.created_at), desc(PipelineEvent.id)).limit(1)
                    )
                    cursor = latest.id if latest else None
                    rows = []
            else:
                rows = list(db.scalars(
                    select(PipelineEvent).order_by(PipelineEvent.created_at, PipelineEvent.id).limit(50)
                ).all())
            frames = []
            if rows:
                for event in rows:
                    cursor = event.id
                    payload = {
                        "id": event.id,
                        "type": event.event_type,
                        "layer": event.layer_key,
                        "layer_version": event.layer_version,
                        "created_at": _iso(event.created_at),
                        **event.payload,
                    }
                    wire_event = "data-update" if event.event_type == "layer.published" else event.event_type
                    frames.append("id: %s\nevent: %s\ndata: %s\n\n" % (event.id, wire_event, json.dumps(payload, ensure_ascii=False)))
            return cursor, frames
        finally:
            # Materialize frames before returning the connection. A small
            # managed pool must remain available while clients receive data.
            db.rollback()

    async def generate():
        # A synchronous pool checkout can wait for another request's cleanup.
        # Keep that wait off the event loop so the other request can finish.
        # Calls are awaited sequentially; this Session is never used concurrently.
        cursor = await run_in_threadpool(initial_cursor, after or request.headers.get("Last-Event-ID"))
        yield "retry: 5000\n: connected\n\n"
        while not await request.is_disconnected():
            cursor, frames = await run_in_threadpool(read_frames, cursor)
            for frame in frames or [": heartbeat\n\n"]:
                yield frame
            import asyncio

            await asyncio.sleep(5)

    # Next.js and intermediate proxies must not gzip/coalesce small SSE frames.
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@router.get("/system/runtime")
def system_runtime() -> Dict[str, Any]:
    return runtime_status()


@router.get("/system/refresh-schedule")
def refresh_schedule(db: Session = Depends(get_db)) -> Dict[str, Any]:
    sources = db.scalars(select(DataSource).where(DataSource.enabled.is_(True)).order_by(DataSource.check_interval_seconds)).all()
    return {
        "timezone": "UTC",
        "dispatcher_interval_seconds": settings.scheduler_tick_seconds,
        "first_check": "next_dispatcher_tick_after_dependencies_are_ready",
        "items": [
            {
                "source_name": source.name,
                "interval_seconds": source.check_interval_seconds,
                "stale_after_seconds": source.stale_after_seconds,
                "schedule_managed_by": "Celery Beat + durable due-source dispatcher",
            }
            for source in sources
        ],
    }


@router.get("/system/readiness")
def system_readiness(
    response: Response,
    probe_dependencies: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Audit production configuration without returning secrets or endpoints."""
    response.headers["Cache-Control"] = "no-store"
    return production_readiness(db, probe_dependencies=probe_dependencies)


@router.get("/system/data-readiness")
def system_data_readiness(db: Session = Depends(get_db)) -> Response:
    # The full readiness audit runs dozens of queries against a remote database
    # (about 9 s); any publication, check or job invalidates it immediately, and
    # approvals made by admins show up within two minutes.
    return _cached_dashboard_response(db, "system/data-readiness", lambda: published_data_readiness(db), seconds=120)
