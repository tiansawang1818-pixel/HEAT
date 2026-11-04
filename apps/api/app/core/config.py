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
    redis_url: str = "redis://redis:6379/0"
    admin_api_key: str = "change-me-in-production"
    demo_mode: bool = False
    allow_fixture_ingestion: bool = False
    fixture_root: Path = Path("/workspace/data/fixtures")
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    log_level: str = "INFO"
    bangkok_bbox: List[float] = Field(default_factory=lambda: [100.327, 13.494, 100.938, 13.955])
    gee_project: Optional[str] = None
    open_meteo_latitude: float = 13.7563
    open_meteo_longitude: float = 100.5018
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    source_request_timeout_seconds: int = 30
    worldpop_api_base_url: str = "https://api.worldpop.org/v3"
    worldpop_api_key: Optional[str] = None
    worldpop_reference_year: Optional[int] = None
    osm_replication_state_url: str = "https://planet.openstreetmap.org/replication/day/state.txt"
    stale_weather_hours: int = 3
    stale_satellite_days: int = 32

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore", env_ignore_empty=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
