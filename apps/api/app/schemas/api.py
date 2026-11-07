from datetime import date, datetime, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


Identifier = Annotated[str, Field(min_length=1, max_length=64)]


class OrmModel(ApiModel):
    model_config = ConfigDict(from_attributes=True)


class Pagination(ApiModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)


class OptimizationRequest(ApiModel):
    budget_thb: int = Field(ge=100_000, le=100_000_000)
    objective: Literal["balanced", "beneficiaries", "priority", "cost_effectiveness"] = "balanced"
    enabled_types: Optional[List[str]] = None
    candidate_ids: Optional[List[Identifier]] = Field(default=None, max_length=500)
    weights: Optional[Dict[str, float]] = None

    @field_validator("weights")
    @classmethod
    def validate_weights(cls, value: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
        if value is not None and set(value) - {"beneficiaries", "priority", "benefit", "cost_effectiveness"}:
            raise ValueError("Unknown optimization objective weight")
        if value is not None and (any(weight < 0 for weight in value.values()) or sum(value.values()) <= 0):
            raise ValueError("Weights must be non-negative with a positive total")
        return value


class ScenarioCompareRequest(ApiModel):
    budget_thb: int = Field(ge=100_000, le=100_000_000)
    enabled_types: Optional[List[str]] = None
    candidate_ids: Optional[List[Identifier]] = Field(default=None, max_length=500)


class ScenarioSaveRequest(ScenarioCompareRequest):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Scenario name must not be blank")
        return value


class ForecastRequest(ApiModel):
    horizon_years: Literal[1, 3, 5]
    area_ids: Optional[List[Identifier]] = Field(default=None, max_length=500)


class AssistantQuery(ApiModel):
    question: str = Field(min_length=2, max_length=1000)
    area_id: Optional[Identifier] = None
    budget_thb: Optional[int] = Field(default=None, ge=100_000, le=100_000_000)


class IngestionRequest(ApiModel):
    source: Literal["bma-boundaries", "landsat", "sentinel", "dynamic-world", "open-meteo", "worldpop", "osm", "fixture"]
    fixture_path: Optional[str] = None


class BackfillBudget(ApiModel):
    max_assets: int = Field(default=12, ge=1, le=100)
    max_catalogue_requests: int = Field(default=50, ge=1, le=500)
    step_delay_seconds: int = Field(default=600, ge=60, le=86400)


class BackfillRequest(BackfillBudget):
    source: Literal["landsat", "sentinel", "dynamic-world"]
    start_date: date
    end_date: date
    page_size: int = Field(default=10, ge=1, le=25)
    window_days: int = Field(default=16, ge=1, le=32)

    @model_validator(mode="after")
    def frozen_range(self) -> "BackfillRequest":
        if self.start_date >= self.end_date or (self.end_date - self.start_date).days > 3660:
            raise ValueError("Historical range must be positive and at most ten years")
        if self.end_date > datetime.now(timezone.utc).date():
            raise ValueError("Historical end date is exclusive and cannot be in the future")
        return self


class PriorityCalculateRequest(ApiModel):
    temperature_anomaly: Optional[float] = None
    population_exposure: Optional[float] = None
    vegetation_deficit: Optional[float] = None
    built_intensity: Optional[float] = None
    historical_change: Optional[float] = None
    weights: Optional[Dict[str, float]] = None


InterventionType = Literal[
    "tree_planting",
    "public_shade_structure",
    "shaded_walkway",
    "cooling_rest_area",
    "reflective_surface",
]


class CostAssumptionCreateRequest(ApiModel):
    intervention_type: InterventionType
    version: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._-]+$")
    unit_cost_thb: float = Field(gt=0, le=100_000_000)
    annual_maintenance_thb: Optional[float] = Field(default=None, ge=0, le=100_000_000)
    cost_basis: Literal["fixed_per_site"] = "fixed_per_site"
    evidence_url: HttpUrl
    evidence_title: str = Field(min_length=3, max_length=300)
    notes: Optional[str] = Field(default=None, max_length=2000)
    effective_from: Optional[datetime] = None


class CandidateApprovalRequest(ApiModel):
    cost_assumption_id: str = Field(min_length=1, max_length=36)
    confirm_site_feasibility: Literal[True]
    confirm_land_control: Literal[True]
    confirm_design_accessibility_review: Literal[True]
    feasibility_evidence_url: HttpUrl
    feasibility_notes: str = Field(min_length=10, max_length=2000)
    estimated_beneficiaries: Optional[float] = Field(default=None, ge=0)
    benefit_score: Optional[float] = Field(default=None, ge=0, le=100)
    benefit_evidence_url: Optional[HttpUrl] = None

    @model_validator(mode="after")
    def require_benefit_evidence(self) -> "CandidateApprovalRequest":
        if (self.estimated_beneficiaries is not None or self.benefit_score is not None) and not self.benefit_evidence_url:
            raise ValueError("benefit_evidence_url is required when benefit values are supplied")
        return self
