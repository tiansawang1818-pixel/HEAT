import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import (
    AreaIndicator,
    CandidateIntervention,
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
    SourceCheck,
    TemperatureObservation,
)
from app.schemas.api import AssistantQuery, ForecastRequest, IngestionRequest, OptimizationRequest, PriorityCalculateRequest, ScenarioCompareRequest
from app.services.connectors import (
    DynamicWorldConnector,
    FixtureConnector,
    LandsatConnector,
    OpenMeteoConnector,
    OpenStreetMapConnector,
    SentinelConnector,
    WorldPopConnector,
)
from app.services.optimizer import Candidate, optimize_portfolio
from app.services.pipeline import run_monitor
from app.services.priority import calculate_priority, sensitivity_range


router = APIRouter()


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return _aware(value).isoformat() if value else None


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


def _geometry_intersects_bbox(geometry: Optional[Dict[str, Any]], bbox: Optional[tuple[float, float, float, float]]) -> bool:
    """Bounds check used by SQLite fixtures and the Bangkok pilot API.

    A large production deployment should push the equivalent predicate down to
    PostGIS with ST_Intersects or serve MVTs instead of loading all geometries.
    """
    if bbox is None:
        return True
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


def _source_status(db: Session, source: DataSource) -> Dict[str, Any]:
    latest_any = db.scalar(select(DatasetVersion).where(DatasetVersion.source_id == source.id).order_by(desc(DatasetVersion.discovered_at)).limit(1))
    latest = db.scalar(
        select(DatasetVersion)
        .where(DatasetVersion.source_id == source.id, DatasetVersion.status == "published")
        .order_by(desc(DatasetVersion.observed_at))
        .limit(1)
    )
    check = db.scalar(select(SourceCheck).where(SourceCheck.source_id == source.id).order_by(desc(SourceCheck.checked_at)).limit(1))
    now = datetime.now(timezone.utc)
    reason = "ยังไม่มีข้อมูลที่ผ่านการตรวจสอบ"
    status = "unavailable"
    if latest_any and latest_any.status in {"staged", "validating", "processing", "publishing"}:
        status, reason = "updating", "กำลังตรวจสอบและประมวลผลเวอร์ชันใหม่"
    elif latest_any and latest_any.status == "failed" and (not latest or _aware(latest_any.discovered_at) > _aware(latest.published_at)):
        status, reason = "error", "เวอร์ชันล่าสุดไม่ผ่านการตรวจสอบ; ระบบยังคงใช้เวอร์ชันก่อนหน้า"
    elif latest and latest.observed_at:
        age = (now - _aware(latest.observed_at)).total_seconds()
        if age > source.stale_after_seconds:
            status, reason = "stale", "อายุข้อมูลเกินเกณฑ์ %s ชั่วโมง" % round(source.stale_after_seconds / 3600)
        else:
            status, reason = "up_to_date", "เป็นเวอร์ชันล่าสุดที่ตรวจสอบและเผยแพร่แล้ว"
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


@router.get("/datasets")
def datasets(db: Session = Depends(get_db)) -> Dict[str, Any]:
    sources = db.scalars(select(DataSource).where(DataSource.source_type != "test_fixture").order_by(DataSource.name)).all()
    return {"items": [_source_status(db, source) for source in sources], "count": len(sources)}


@router.get("/datasets/status")
def dataset_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    sources = db.scalars(select(DataSource).where(DataSource.source_type != "test_fixture").order_by(DataSource.name)).all()
    items = [_source_status(db, source) for source in sources]
    return {
        "items": items,
        "summary": {status: sum(1 for item in items if item["status"] == status) for status in ["up_to_date", "updating", "stale", "unavailable", "error"]},
    }


@router.get("/weather/context")
def weather_context(db: Session = Depends(get_db)) -> Dict[str, Any]:
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
                "centroid": row.centroid,
                "area_sq_km": row.area_sq_km,
                "quality_status": row.quality_status,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def _latest_indicators(db: Session, area_id: str) -> Dict[str, Dict[str, Any]]:
    rows = db.scalars(select(AreaIndicator).where(AreaIndicator.area_id == area_id).order_by(desc(AreaIndicator.observed_at))).all()
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
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
def area_detail(area_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    area = db.get(GeographicArea, area_id)
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    score = db.scalar(select(PriorityScore).where(PriorityScore.area_id == area_id).order_by(desc(PriorityScore.calculated_at)).limit(1))
    candidates = db.scalars(select(CandidateIntervention).where(CandidateIntervention.area_id == area_id, CandidateIntervention.active.is_(True))).all()
    return {
        "id": area.id,
        "name_th": area.name_th,
        "name_en": area.name_en,
        "geometry": area.geometry,
        "centroid": area.centroid,
        "boundary_provenance": {"source": area.source_name, "version": area.source_version, "quality_status": area.quality_status},
        "indicators": _latest_indicators(db, area_id),
        "priority": None
        if not score
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
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if not db.get(GeographicArea, area_id):
        raise HTTPException(status_code=404, detail="Area not found")
    query = select(AreaIndicator).where(AreaIndicator.area_id == area_id)
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
def map_layers(db: Session = Depends(get_db)) -> Dict[str, Any]:
    versions = {item["dataset_id"]: item for item in datasets(db)["items"]}
    source_ids = {
        "temperature": ["LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2"],
        "population": ["worldpop/global"],
        "vegetation": ["GOOGLE/DYNAMICWORLD/V1"],
        "ndvi": ["COPERNICUS/S2_SR_HARMONIZED"],
        "built": ["GOOGLE/DYNAMICWORLD/V1"],
        "priority": [],
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
        return bool(db.scalar(select(func.count()).select_from(AreaIndicator).where(AreaIndicator.indicator_key == indicator)))

    return {
        "items": [
            {
                "id": key,
                "name_th": name,
                "indicator_key": indicator,
                "unit": unit,
                "feature_url": "/api/map/features?layer=%s" % key,
                "delivery": "viewport_geojson",
                "source_name": source_labels[key],
                "status": "up_to_date" if layer_has_data(key, indicator) else "unavailable",
                "status_reason": "มี geographic records ที่ผ่านการเผยแพร่" if layer_has_data(key, indicator) else "ยังไม่มี geographic records ที่ผ่านการเผยแพร่สำหรับชั้นนี้",
                "source_status": {dataset_id: versions[dataset_id] for dataset_id in source_ids[key] if dataset_id in versions},
            }
            for key, name, indicator, unit in LAYER_DEFINITIONS
        ]
    }


@router.get("/map/features")
def map_features(
    layer: str = Query(default="priority"),
    bbox: Optional[str] = None,
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    known = {item[0]: item for item in LAYER_DEFINITIONS}
    if layer not in known:
        raise HTTPException(status_code=400, detail="Unknown layer")
    parsed_bbox = _parse_bbox(bbox)
    features: List[Dict[str, Any]] = []
    if layer == "candidates":
        rows = db.scalars(select(CandidateIntervention).where(CandidateIntervention.active.is_(True))).all()
        for row in rows:
            if not _geometry_intersects_bbox(row.geometry, parsed_bbox):
                continue
            features.append(
                {
                    "type": "Feature",
                    "id": row.id,
                    "geometry": row.geometry,
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
            if len(features) >= limit:
                break
    else:
        indicator_key = known[layer][2]
        areas_rows = db.scalars(select(GeographicArea)).all()
        for area in areas_rows:
            if not _geometry_intersects_bbox(area.geometry, parsed_bbox):
                continue
            if layer == "priority":
                score = db.scalar(select(PriorityScore).where(PriorityScore.area_id == area.id).order_by(desc(PriorityScore.calculated_at)).limit(1))
                value, observed_at, quality = (score.score, score.calculated_at, "derived") if score else (None, None, "unavailable")
            elif layer == "future-risk":
                forecast = db.scalar(select(ForecastRun).order_by(desc(ForecastRun.created_at)).limit(1))
                projections = forecast.results.get("projections", []) if forecast and forecast.results else []
                projection = next((item for item in projections if item.get("area_id") == area.id), None)
                value, observed_at, quality = (
                    (projection.get("scenario_change_c"), forecast.created_at, "scenario_projection")
                    if projection
                    else (None, None, "unavailable")
                )
            else:
                observation = db.scalar(
                    select(AreaIndicator)
                    .where(AreaIndicator.area_id == area.id, AreaIndicator.indicator_key == indicator_key)
                    .order_by(desc(AreaIndicator.observed_at))
                    .limit(1)
                )
                value, observed_at, quality = (
                    (observation.value, observation.observed_at, observation.quality_status) if observation else (None, None, "unavailable")
                )
            features.append(
                {
                    "type": "Feature",
                    "id": area.id,
                    "geometry": area.geometry,
                    "properties": {
                        "area_id": area.id,
                        "name_th": area.name_th,
                        "name_en": area.name_en,
                        "value": value,
                        "unit": known[layer][3],
                        "observed_at": _iso(observed_at),
                        "quality_status": quality,
                        "scenario_horizon_years": forecast.horizon_years if layer == "future-risk" and forecast and projection else None,
                        "scenario_uncertainty_c": projection.get("uncertainty_c") if layer == "future-risk" and projection else None,
                    },
                }
            )
            if len(features) >= limit:
                break
    return {"type": "FeatureCollection", "features": features, "metadata": {"layer": layer, "bbox_filter": list(parsed_bbox) if parsed_bbox else None, "count": len(features)}}


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
    query = select(CandidateIntervention).where(CandidateIntervention.active.is_(True))
    if ids:
        query = query.where(CandidateIntervention.id.in_(ids))
    return list(db.scalars(query).all())


def _to_optimizer_candidates(rows: List[CandidateIntervention]) -> List[Candidate]:
    return [
        Candidate(
            id=row.id,
            cost_thb=int(round(row.estimated_cost_thb)),
            beneficiaries=float(row.estimated_beneficiaries or 0),
            priority_score=float(row.priority_score),
            benefit_score=float(row.benefit_score or 0),
            intervention_type=row.intervention_type,
            feasible=row.feasibility_status == "eligible",
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
    result = {}
    for source in db.scalars(select(DataSource)).all():
        latest = db.scalar(
            select(DatasetVersion)
            .where(DatasetVersion.source_id == source.id, DatasetVersion.status == "published")
            .order_by(desc(DatasetVersion.published_at))
            .limit(1)
        )
        if latest:
            result[source.dataset_id] = {"version_id": latest.id, "source_version": latest.source_version, "layer_version": latest.layer_version}
    return result


def _serialize_optimization_run(db: Session, run: OptimizationRun) -> Dict[str, Any]:
    allocation_rows = db.scalars(
        select(OptimizationAllocation)
        .where(OptimizationAllocation.run_id == run.id)
        .order_by(OptimizationAllocation.rank)
    ).all()
    selected = []
    estimated_beneficiaries = 0.0
    for allocation in allocation_rows:
        candidate = db.get(CandidateIntervention, allocation.candidate_id)
        if candidate is None:
            continue
        estimated_beneficiaries += float(candidate.estimated_beneficiaries or 0)
        selected.append(
            {
                "id": candidate.id,
                "area_id": candidate.area_id,
                "name_th": candidate.name_th,
                "intervention_type": candidate.intervention_type,
                "geometry": candidate.geometry,
                "estimated_cost_thb": candidate.estimated_cost_thb,
                "annual_maintenance_thb": candidate.annual_maintenance_thb,
                "estimated_beneficiaries": candidate.estimated_beneficiaries,
                "priority_score": candidate.priority_score,
                "objective_contribution": allocation.objective_contribution,
                "feasibility_constraints": candidate.feasibility_constraints,
                "evidence": candidate.evidence,
                "uncertainty": candidate.uncertainty,
                "explanation": allocation.explanation,
            }
        )
    return {
        "run_id": run.id,
        "status": run.status,
        "budget_thb": run.budget_thb,
        "allocated_thb": run.allocated_thb,
        "unallocated_thb": max(0, run.budget_thb - run.allocated_thb),
        "estimated_beneficiaries": estimated_beneficiaries,
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
                "geometry": row.geometry,
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
        "limitations": ["ผลประโยชน์เป็นค่าประมาณตามหลักฐานใน candidate แต่ละรายการ", "ไม่ตีความคะแนนความร้อนเป็นฐานะทางเศรษฐกิจ"],
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
          <td>{rank}</td><td>{name}</td><td>{kind}</td><td class="number">{cost:,.0f}</td>
          <td class="number">{beneficiaries:,.0f}</td><td class="number">{priority:.1f}</td>
        </tr>
        <tr class="detail"><td></td><td colspan="5"><strong>พื้นที่</strong> {area_id}<br><strong>เรขาคณิต</strong><pre>{geometry}</pre><strong>หลักฐาน</strong><pre>{evidence}</pre><strong>ความไม่แน่นอน</strong><pre>{uncertainty}</pre></td></tr>
        """.format(
            rank=index,
            name=text(item["name_th"]),
            kind=text(item["intervention_type"]),
            cost=float(item["estimated_cost_thb"] or 0),
            beneficiaries=float(item["estimated_beneficiaries"] or 0),
            priority=float(item["priority_score"] or 0),
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
  <tr><td><strong>งบประมาณ</strong><br>฿{budget:,.0f}</td><td><strong>จัดสรรแล้ว</strong><br>฿{allocated:,.0f}</td><td><strong>ผู้รับประโยชน์โดยประมาณ</strong><br>{beneficiaries:,.0f}</td></tr>
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
        beneficiaries=float(report["estimated_beneficiaries"] or 0),
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
    candidate_rows = _candidate_rows(db, request.candidate_ids)
    candidates = _to_optimizer_candidates(candidate_rows)
    candidates_by_id = {candidate.id: candidate for candidate in candidate_rows}
    definitions = [
        ("hottest_first", "ร้อนที่สุดก่อน", {"beneficiaries": 0.05, "priority": 0.85, "benefit": 0.05, "cost_effectiveness": 0.05}),
        ("population_first", "ประชากรก่อน", {"beneficiaries": 0.85, "priority": 0.05, "benefit": 0.05, "cost_effectiveness": 0.05}),
        ("heatshift_optimized", "HEAT//SHIFT สมดุล", _objective_weights("balanced", None)),
    ]
    results = []
    for key, label, weights in definitions:
        portfolio = optimize_portfolio(candidates, request.budget_thb, weights, request.enabled_types)
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
                    "geometry": candidate.geometry,
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
                "weights": weights,
            }
        )
    return {
        "budget_thb": request.budget_thb,
        "scenarios": results,
        "input_versions": _current_versions(db),
        "eligibility_consistent": True,
        "comparison_note_th": "ทุกยุทธศาสตร์ใช้ candidate, feasibility และเพดานงบชุดเดียวกัน ต่างกันเฉพาะน้ำหนัก objective",
    }


@router.post("/forecast/run")
def forecast_run(request: ForecastRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    query = select(TemperatureObservation).order_by(TemperatureObservation.observed_at)
    if request.area_ids:
        query = query.where(TemperatureObservation.area_id.in_(request.area_ids))
    rows = db.scalars(query).all()
    years = len({row.observed_at.year for row in rows})
    if years < 5:
        result = {
            "status": "insufficient_data",
            "mode": "scenario_only",
            "message_th": "ยังมีข้อมูลย้อนหลังไม่เพียงพอสำหรับพยากรณ์ห้าปีอย่างน่าเชื่อถือ",
            "available_years": years,
            "required_years": 5,
            "projections": [],
        }
        method = "guardrail-insufficient-data"
    else:
        grouped: Dict[str, List[TemperatureObservation]] = {}
        for row in rows:
            grouped.setdefault(row.area_id, []).append(row)
        projections = []
        for area_id, observations in grouped.items():
            first, last = observations[0], observations[-1]
            elapsed = max((last.observed_at - first.observed_at).days / 365.25, 1.0)
            slope = ((last.lst_c_mean or 0) - (first.lst_c_mean or 0)) / elapsed
            projections.append(
                {
                    "area_id": area_id,
                    "horizon_years": request.horizon_years,
                    "scenario_change_c": round(slope * request.horizon_years, 2),
                    "uncertainty_c": round(max(0.5, abs(slope) * request.horizon_years), 2),
                    "label": "scenario_projection_not_forecast",
                }
            )
        result = {"status": "completed", "mode": "trend_scenario", "projections": projections}
        method = "transparent-linear-trend-scenario-v1"
    run = ForecastRun(
        horizon_years=request.horizon_years,
        method=method,
        model_version="scenario-v1",
        training_versions=_current_versions(db),
        assumptions={"temporal_holdout_required_for_model": True, "spatial_holdout_required_for_model": True},
        results=result,
    )
    db.add(run)
    db.commit()
    return {
        "run_id": run.id,
        **result,
        "method": run.method,
        "model_version": run.model_version,
        "training_versions": run.training_versions,
        "assumptions": run.assumptions,
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
                            f"{optimization['estimated_beneficiaries']:,.0f}",
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
        sources = [_source_status(db, source) for source in db.scalars(select(DataSource)).all()]
        observed = [item for item in sources if item["last_source_observation"]]
        if not observed:
            answer = "ขณะนี้ยังไม่มีข้อมูลที่ผ่านการตรวจสอบและเผยแพร่ จึงไม่ควรระบุวันที่สังเกตการณ์"
        else:
            latest = max(observed, key=lambda item: item["last_source_observation"])
            answer = "ข้อมูลที่มีเวลาสังเกตการณ์ล่าสุดมาจาก %s เมื่อ %s สถานะ %s" % (
                latest["source_name"], latest["last_source_observation"], latest["status"]
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
                component_text = ", ".join("%s=%s" % (key, value) for key, value in score.components.items() if value is not None)
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
                area.name_th, score.score, _iso(score.calculated_at)
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
        job.status, job.stage, job.started_at, job.progress = "running", "source_check", datetime.now(timezone.utc), 0.1
        db.commit()
        result = run_monitor(db, _connector_for(source, fixture_path), requested_by="admin")
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
                "error_message": row.error_message,
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
    for source in sources:
        check = db.scalar(
            select(SourceCheck).where(SourceCheck.source_id == source.id).order_by(desc(SourceCheck.checked_at)).limit(1)
        )
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
                "error_message": check.error_message if check else None,
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
            "sources_with_errors": sum(1 for item in source_checks if item["status"] == "failed"),
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
                "error_message": item.error_message,
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
            "error_message": job.error_message,
            "finished_at": _iso(job.finished_at),
        },
        "last_event": None
        if not last_event
        else {"type": last_event.event_type, "layer": last_event.layer_key, "layer_version": last_event.layer_version, "created_at": _iso(last_event.created_at)},
    }


@router.post("/ingestion/run", dependencies=[Depends(require_admin)], status_code=202)
def ingestion_run(request: IngestionRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> Dict[str, Any]:
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
    job = IngestionJob(job_type="manual_refresh", status="queued", stage="queued", requested_by="admin")
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
        if settings.environment == "production":
            job.status = "failed"
            job.stage = "dispatch_failed"
            job.error_message = "Durable queue is unavailable; job was not executed"
            db.commit()
            raise HTTPException(status_code=503, detail="Durable ingestion queue is unavailable")
        background_tasks.add_task(_run_manual_job, job.id, request.source, fixture_path)
    return {"job_id": job.id, "status": "queued", "status_url": "/api/ingestion/status?job_id=%s" % job.id, "dispatch_mode": dispatch_mode}


@router.get("/events/stream")
async def events_stream(request: Request, after: Optional[str] = None, db: Session = Depends(get_db)) -> StreamingResponse:
    async def generate():
        cursor = after
        while not await request.is_disconnected():
            query = select(PipelineEvent).order_by(PipelineEvent.created_at).limit(50)
            if cursor:
                query = query.where(PipelineEvent.id != cursor, PipelineEvent.created_at >= select(PipelineEvent.created_at).where(PipelineEvent.id == cursor).scalar_subquery())
            rows = db.scalars(query).all()
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
                    yield "id: %s\nevent: %s\ndata: %s\n\n" % (event.id, wire_event, json.dumps(payload, ensure_ascii=False))
            else:
                yield ": heartbeat\n\n"
            import asyncio

            await asyncio.sleep(5)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/system/refresh-schedule")
def refresh_schedule(db: Session = Depends(get_db)) -> Dict[str, Any]:
    sources = db.scalars(select(DataSource).where(DataSource.enabled.is_(True)).order_by(DataSource.check_interval_seconds)).all()
    return {
        "timezone": "UTC",
        "items": [
            {
                "source_name": source.name,
                "interval_seconds": source.check_interval_seconds,
                "stale_after_seconds": source.stale_after_seconds,
                "schedule_managed_by": "Celery Beat",
            }
            for source in sources
        ],
    }
