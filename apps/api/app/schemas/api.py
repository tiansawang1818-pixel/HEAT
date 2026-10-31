from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Pagination(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)


class OptimizationRequest(BaseModel):
    budget_thb: int = Field(ge=100_000, le=100_000_000)
    objective: Literal["balanced", "beneficiaries", "priority", "cost_effectiveness"] = "balanced"
    enabled_types: Optional[List[str]] = None
    candidate_ids: Optional[List[str]] = None
    weights: Optional[Dict[str, float]] = None

    @field_validator("weights")
    @classmethod
    def validate_weights(cls, value: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
        if value is not None and (any(weight < 0 for weight in value.values()) or sum(value.values()) <= 0):
            raise ValueError("Weights must be non-negative with a positive total")
        return value


class ScenarioCompareRequest(BaseModel):
    budget_thb: int = Field(ge=100_000, le=100_000_000)
    enabled_types: Optional[List[str]] = None
    candidate_ids: Optional[List[str]] = None


class ForecastRequest(BaseModel):
    horizon_years: Literal[1, 3, 5]
    area_ids: Optional[List[str]] = None


class AssistantQuery(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    area_id: Optional[str] = None
    budget_thb: Optional[int] = Field(default=None, ge=100_000, le=100_000_000)


class IngestionRequest(BaseModel):
    source: Literal["landsat", "sentinel", "dynamic-world", "open-meteo", "worldpop", "osm", "fixture"]
    fixture_path: Optional[str] = None


class PriorityCalculateRequest(BaseModel):
    temperature_anomaly: Optional[float] = None
    population_exposure: Optional[float] = None
    vegetation_deficit: Optional[float] = None
    built_intensity: Optional[float] = None
    historical_change: Optional[float] = None
    weights: Optional[Dict[str, float]] = None
