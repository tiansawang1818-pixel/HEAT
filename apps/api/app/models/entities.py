import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


def geometry_type(kind: str = "GEOMETRY") -> Any:
    if settings.database_url.startswith("postgresql"):
        from geoalchemy2 import Geometry

        return Geometry(kind, srid=4326, spatial_index=False)
    return JSON


class ProcessingStatus(str, enum.Enum):
    idle = "idle"
    checking = "checking"
    staged = "staged"
    validating = "validating"
    processing = "processing"
    publishing = "publishing"
    published = "published"
    skipped = "skipped"
    failed = "failed"


class DataSource(Base):
    __tablename__ = "data_sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(120))
    dataset_id: Mapped[str] = mapped_column(String(180), unique=True)
    source_type: Mapped[str] = mapped_column(String(50))
    check_interval_seconds: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stale_after_seconds: Mapped[int] = mapped_column(Integer)
    license_url: Mapped[Optional[str]] = mapped_column(String(500))
    config: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    versions: Mapped[List["DatasetVersion"]] = relationship(back_populates="source")


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("source_id", "source_asset_id", name="uq_source_asset"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id"), index=True)
    source_asset_id: Mapped[str] = mapped_column(String(500), index=True)
    source_version: Mapped[str] = mapped_column(String(160))
    checksum: Mapped[Optional[str]] = mapped_column(String(128))
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ingested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default=ProcessingStatus.staged.value)
    quality_status: Mapped[str] = mapped_column(String(30), default="pending")
    quality_summary: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    geographic_extent: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    layer_version: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[DataSource] = relationship(back_populates="versions")


class SourceCheck(Base):
    __tablename__ = "source_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id"), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="running")
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, default=0)
    provider_quota: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[Optional[str]] = mapped_column(ForeignKey("data_sources.id"), index=True)
    dataset_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(50), default="monitor")
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(30), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    requested_by: Mapped[str] = mapped_column(String(80), default="scheduler")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class GeographicArea(Base):
    __tablename__ = "geographic_areas"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name_th: Mapped[str] = mapped_column(String(180), index=True)
    name_en: Mapped[str] = mapped_column(String(180), index=True)
    area_type: Mapped[str] = mapped_column(String(40), default="district")
    parent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("geographic_areas.id"))
    geometry: Mapped[Dict[str, Any]] = mapped_column(geometry_type("MULTIPOLYGON"))
    centroid: Mapped[Optional[Dict[str, Any]]] = mapped_column(geometry_type("POINT"))
    area_sq_km: Mapped[Optional[float]] = mapped_column(Float)
    source_name: Mapped[str] = mapped_column(String(120), default="Bangkok Metropolitan Administration")
    source_version: Mapped[Optional[str]] = mapped_column(String(120))
    quality_status: Mapped[str] = mapped_column(String(30), default="verified")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DistrictBoundary(Base):
    __tablename__ = "district_boundaries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("geographic_areas.id"), index=True)
    dataset_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("dataset_versions.id"))
    geometry: Mapped[Dict[str, Any]] = mapped_column(geometry_type("MULTIPOLYGON"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class RasterAssetMetadata(Base):
    __tablename__ = "raster_asset_metadata"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    storage_uri: Mapped[str] = mapped_column(String(1000))
    media_type: Mapped[str] = mapped_column(String(120), default="image/tiff; application=geotiff; profile=cloud-optimized")
    crs: Mapped[str] = mapped_column(String(60), default="EPSG:4326")
    bounds: Mapped[Dict[str, Any]] = mapped_column(JSON)
    resolution_m: Mapped[Optional[float]] = mapped_column(Float)
    band_metadata: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)


class TemperatureObservation(Base):
    __tablename__ = "temperature_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("geographic_areas.id"), index=True)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lst_c_mean: Mapped[Optional[float]] = mapped_column(Float)
    lst_c_p90: Mapped[Optional[float]] = mapped_column(Float)
    coverage_fraction: Mapped[float] = mapped_column(Float)
    uncertainty_c: Mapped[Optional[float]] = mapped_column(Float)


class VegetationObservation(Base):
    __tablename__ = "vegetation_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("geographic_areas.id"), index=True)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ndvi_mean: Mapped[Optional[float]] = mapped_column(Float)
    tree_cover_fraction: Mapped[Optional[float]] = mapped_column(Float)
    built_fraction: Mapped[Optional[float]] = mapped_column(Float)
    confidence: Mapped[Optional[float]] = mapped_column(Float)


class PopulationEstimate(Base):
    __tablename__ = "population_estimates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("geographic_areas.id"), index=True)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    reference_year: Mapped[int] = mapped_column(Integer, index=True)
    population: Mapped[float] = mapped_column(Float)
    density_per_sq_km: Mapped[Optional[float]] = mapped_column(Float)
    uncertainty_fraction: Mapped[Optional[float]] = mapped_column(Float)


class AreaIndicator(Base):
    __tablename__ = "area_indicators"
    __table_args__ = (Index("ix_indicator_area_key_time", "area_id", "indicator_key", "observed_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("geographic_areas.id"), index=True)
    dataset_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    indicator_key: Mapped[str] = mapped_column(String(80))
    value: Mapped[Optional[float]] = mapped_column(Float)
    unit: Mapped[Optional[str]] = mapped_column(String(50))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    quality_status: Mapped[str] = mapped_column(String(30), default="valid")
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)


class PriorityScore(Base):
    __tablename__ = "priority_scores"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("geographic_areas.id"), index=True)
    score: Mapped[float] = mapped_column(Float, index=True)
    components: Mapped[Dict[str, Any]] = mapped_column(JSON)
    weights: Mapped[Dict[str, Any]] = mapped_column(JSON)
    missing_inputs: Mapped[List[str]] = mapped_column(JSON, default=list)
    input_versions: Mapped[Dict[str, Any]] = mapped_column(JSON)
    formula_version: Mapped[str] = mapped_column(String(50), default="priority-v1")
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CandidateIntervention(Base):
    __tablename__ = "candidate_interventions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    area_id: Mapped[str] = mapped_column(ForeignKey("geographic_areas.id"), index=True)
    name_th: Mapped[str] = mapped_column(String(200))
    intervention_type: Mapped[str] = mapped_column(String(80), index=True)
    geometry: Mapped[Dict[str, Any]] = mapped_column(geometry_type("GEOMETRY"))
    estimated_cost_thb: Mapped[float] = mapped_column(Float)
    annual_maintenance_thb: Mapped[Optional[float]] = mapped_column(Float)
    estimated_beneficiaries: Mapped[Optional[float]] = mapped_column(Float)
    priority_score: Mapped[float] = mapped_column(Float)
    benefit_score: Mapped[Optional[float]] = mapped_column(Float)
    feasibility_status: Mapped[str] = mapped_column(String(40), default="eligible")
    feasibility_constraints: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    uncertainty: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    dataset_versions: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CostAssumption(Base):
    __tablename__ = "cost_assumptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    intervention_type: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(3), default="THB")
    assumptions: Mapped[Dict[str, Any]] = mapped_column(JSON)
    evidence_url: Mapped[Optional[str]] = mapped_column(String(1000))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OptimizationRun(Base):
    __tablename__ = "optimization_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    budget_thb: Mapped[float] = mapped_column(Float)
    allocated_thb: Mapped[float] = mapped_column(Float, default=0)
    objective: Mapped[str] = mapped_column(String(80))
    objective_weights: Mapped[Dict[str, Any]] = mapped_column(JSON)
    objective_value: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default="completed")
    solver: Mapped[str] = mapped_column(String(80))
    input_versions: Mapped[Dict[str, Any]] = mapped_column(JSON)
    is_outdated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class OptimizationAllocation(Base):
    __tablename__ = "optimization_allocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("optimization_runs.id"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidate_interventions.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    objective_contribution: Mapped[float] = mapped_column(Float)
    explanation: Mapped[Dict[str, Any]] = mapped_column(JSON)


class SavedScenario(Base):
    __tablename__ = "saved_scenarios"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    configuration: Mapped[Dict[str, Any]] = mapped_column(JSON)
    dataset_versions: Mapped[Dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    horizon_years: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(80))
    training_versions: Mapped[Dict[str, Any]] = mapped_column(JSON)
    assumptions: Mapped[Dict[str, Any]] = mapped_column(JSON)
    metrics: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    results: Mapped[Dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ValidationReport(Base):
    __tablename__ = "validation_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    checks: Mapped[Dict[str, Any]] = mapped_column(JSON)
    warnings: Mapped[List[str]] = mapped_column(JSON, default=list)
    errors: Mapped[List[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PipelineEvent(Base):
    __tablename__ = "pipeline_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    source_id: Mapped[Optional[str]] = mapped_column(ForeignKey("data_sources.id"), index=True)
    dataset_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    layer_key: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    layer_version: Mapped[Optional[int]] = mapped_column(Integer)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
