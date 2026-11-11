from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "HEAT//SHIFT API"
    environment: str = "development"
    api_prefix: str = "/api"
    database_url: str = "sqlite+pysqlite:///./heatshift.db"
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    # Per sort/hash memory each app connection may use on the managed database.
    # The free Aiven plan has 1 GB for every backend together.
    database_work_mem: str = Field(default="4MB", pattern=r"^[0-9]{1,6}(kB|MB)$")
    redis_url: str = "redis://redis:6379/0"
    admin_api_key: str = "change-me-in-production"
    admin_auth_mode: str = "local_key"
    oidc_issuer: Optional[str] = None
    oidc_audience: Optional[str] = None
    oidc_jwks_url: Optional[str] = None
    oidc_roles_claim: str = "roles"
    oidc_admin_roles: List[str] = Field(default_factory=lambda: ["heatshift.admin"])
    oidc_allowed_algorithms: List[str] = Field(default_factory=lambda: ["RS256"])
    oidc_jwks_cache_seconds: int = 3600
    oidc_http_timeout_seconds: int = 5
    oidc_clock_skew_seconds: int = 30
    rate_limit_enabled: bool = False
    rate_limit_optimization_per_minute: int = 20
    rate_limit_scenario_per_minute: int = 30
    rate_limit_forecast_per_minute: int = 6
    rate_limit_assistant_per_minute: int = 30
    rate_limit_admin_per_minute: int = 15
    rate_limit_fail_closed_admin: bool = True
    demo_mode: bool = False
    allow_fixture_ingestion: bool = False
    fixture_root: Path = Path("/workspace/data/fixtures")
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    log_level: str = "INFO"
    bangkok_bbox: List[float] = Field(default_factory=lambda: [100.327, 13.494, 100.938, 13.955])
    gee_project: Optional[str] = None
    historical_ingestion_enabled: bool = False
    # Forecast-only deployments plan with screened sites and never fund real
    # investments, so per-site field evidence is not a readiness requirement.
    investment_decisions_enabled: bool = False
    gee_tile_descriptor_ttl_seconds: int = 3000
    open_meteo_latitude: float = 13.7563
    open_meteo_longitude: float = 100.5018
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    source_request_timeout_seconds: int = 30
    scheduler_tick_seconds: int = Field(default=60, ge=2, le=300)
    worldpop_api_base_url: str = "https://api.worldpop.org/v3"
    worldpop_api_key: Optional[str] = None
    worldpop_reference_year: Optional[int] = None
    bma_boundary_catalog_url: str = "https://data.bangkok.go.th/api/3/action/package_show"
    bma_boundary_dataset_id: str = "e537025b-1cf6-4c5b-8e46-c2e976f13283"
    bma_boundary_resource_id: str = "d7be7e84-9d84-4595-bf8f-79b0bc01f1ae"
    bma_boundary_max_download_bytes: int = 10_000_000
    bma_boundary_snapshot_dir: Path = Path(__file__).resolve().parents[4] / "data" / "bootstrap" / "bma"
    osm_replication_state_url: str = "https://planet.openstreetmap.org/replication/day/state.txt"
    overpass_api_url: str = "https://overpass-api.de/api/interpreter"
    overpass_timeout_seconds: int = 120
    overpass_max_response_bytes: int = 20_000_000
    overpass_max_elements: int = 20_000
    overpass_grid_divisions: int = 2
    stale_weather_hours: int = 3
    stale_satellite_days: int = 32
    forecast_min_years: int = 5
    forecast_min_areas: int = 5
    forecast_min_validation_samples: int = 20
    forecast_min_baseline_improvement: float = 0.0

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore", env_ignore_empty=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
