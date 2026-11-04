import httpx
import pytest
from sqlalchemy import desc, select

from app.core.config import settings
from app.models.entities import (
    AreaIndicator,
    DatasetVersion,
    GeographicArea,
    PopulationEstimate,
    PriorityScore,
    SourceCheck,
)
from app.services.connectors.base import ProviderRateLimitError
from app.services.connectors.catalogs import WorldPopConnector
from app.services.pipeline import run_monitor


def _response(status: int, url: str, payload, headers=None):
    return httpx.Response(
        status,
        json=payload,
        headers=headers or {},
        request=httpx.Request("GET", url),
    )


def _add_areas(db):
    for area_id, offset in (("district-a", 0.0), ("district-b", 0.03)):
        db.add(
            GeographicArea(
                id=area_id,
                name_th=area_id,
                name_en=area_id,
                geometry={
                    "type": "MultiPolygon",
                    "coordinates": [[[[100.50 + offset, 13.70], [100.52 + offset, 13.70], [100.52 + offset, 13.72], [100.50 + offset, 13.72], [100.50 + offset, 13.70]]]],
                },
            )
        )
    db.commit()


def _catalog(years=None):
    return {
        "dataset": "WorldPop Global2",
        "release": "R2025A",
        "resolution_m": 100,
        "years": years or [2024, 2030],
        "age_sex_years": years or [2024, 2030],
        "countries": 246,
        "units": 4491,
        "licence": {"name": "CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/"},
        "citation": [{"dataset": "Population", "doi": "10.5258/SOTON/WP00839"}],
        "endpoints": [],
        "docs": "https://api.worldpop.org/v3/documentation",
        "llms_txt": "https://api.worldpop.org/v3/llms.txt",
        "mcp": "https://api.worldpop.org/v3/mcp",
        "stac": "https://api.stac.worldpop.org",
    }


def test_worldpop_batch_ingestion_publishes_population_and_quota(db, monkeypatch):
    _add_areas(db)
    monkeypatch.setattr(settings, "worldpop_reference_year", 2024)

    def fake_request(method, url, **kwargs):
        if url.endswith("/catalog"):
            return _response(200, url, _catalog([2024, 2025, 2030]))
        assert method == "POST"
        assert url.endswith("/batch")
        assert kwargs["json"]["year"] == 2024
        assert len(kwargs["json"]["features"]["features"]) == 2
        return _response(
            200,
            url,
            {
                "data_year": 2024,
                "total": {"total_population": 3500.0, "area_km2": 8.0},
                "features": [
                    {"id": "district-a", "name": "district-a", "total_population": 2000.0, "population_density": 500.0, "share_of_total": 0.5714, "rank": 1, "area_km2": 4.0},
                    {"id": "district-b", "name": "district-b", "total_population": 1500.0, "population_density": 375.0, "share_of_total": 0.4286, "rank": 2, "area_km2": 4.0},
                ],
                "processing_time_ms": 82,
            },
            {
                "X-RateLimit-Limit": "1000",
                "X-RateLimit-Remaining": "998",
                "X-RateLimit-Reset": "2026-11-05T00:00:00Z",
            },
        )

    monkeypatch.setattr(httpx, "request", fake_request)
    result = run_monitor(db, WorldPopConnector(), requested_by="test")

    version = db.scalar(select(DatasetVersion).where(DatasetVersion.source_asset_id == "worldpop:R2025A:2024:100m"))
    estimates = db.scalars(select(PopulationEstimate).order_by(PopulationEstimate.area_id)).all()
    density = db.scalars(select(AreaIndicator).where(AreaIndicator.indicator_key == "population_density")).all()
    reference_year = db.scalars(select(AreaIndicator).where(AreaIndicator.indicator_key == "population_reference_year")).all()
    priorities = db.scalars(select(PriorityScore).order_by(PriorityScore.area_id)).all()
    check = db.scalar(select(SourceCheck).order_by(desc(SourceCheck.checked_at)).limit(1))

    assert result["processed"] == 1
    assert version.status == "published"
    assert version.metadata_json["data_class"] == "population_estimate"
    assert version.metadata_json["observation_semantics"] == "reference_year_not_realtime_measurement"
    assert [row.population for row in estimates] == [2000.0, 1500.0]
    assert [row.reference_year for row in estimates] == [2024, 2024]
    assert len(density) == 2
    assert [row.value for row in reference_year] == [2024.0, 2024.0]
    assert len(priorities) == 2
    assert priorities[0].components["population_exposure"] == 500.0
    assert check.provider_quota["remaining"] == "998"


def test_worldpop_default_reference_year_does_not_select_future_projection(monkeypatch):
    monkeypatch.setattr(settings, "worldpop_reference_year", None)
    monkeypatch.setattr(
        httpx,
        "request",
        lambda method, url, **kwargs: _response(200, url, _catalog([2024, 2030])),
    )

    assets = WorldPopConnector().discover()

    assert assets[0].metadata["reference_year"] == 2024
    assert assets[0].metadata["observation_semantics"] == "reference_year_not_realtime_measurement"


def test_worldpop_rate_limit_is_retryable_and_preserves_quota_metadata(monkeypatch):
    connector = WorldPopConnector()
    monkeypatch.setattr(settings, "worldpop_reference_year", 2024)

    def fake_request(method, url, **kwargs):
        if url.endswith("/catalog"):
            return _response(200, url, _catalog([2024]))
        return _response(
            429,
            url,
            {"error": "rate_limit_exceeded"},
            {"X-RateLimit-Limit": "1000", "X-RateLimit-Remaining": "0", "Retry-After": "3600"},
        )

    monkeypatch.setattr(httpx, "request", fake_request)
    asset = connector.discover()[0]

    with pytest.raises(ProviderRateLimitError, match="retry_after_seconds=3600") as captured:
        connector.process(
            asset,
            [{"id": "district-a", "geometry": {"type": "Polygon", "coordinates": [[[100.5, 13.7], [100.6, 13.7], [100.6, 13.8], [100.5, 13.8], [100.5, 13.7]]]}}],
        )

    assert connector.quota_status()["remaining"] == "0"
    assert connector.quota_status()["retry_after_seconds"] == "3600"
    assert connector.quota_status()["status"] == "rate_limited"
    assert captured.value.retry_after_seconds == 3600
