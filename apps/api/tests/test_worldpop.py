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
    SourceProcessingCheckpoint,
)
from app.services.connectors.base import ConnectorError, ProviderRateLimitError
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


def _health(**overrides):
    # Public contract from /v3/health; population answers below are synthetic.
    return {"status": "healthy", "limits": {"max_vertices": 50000, "max_parts": 1000,
        "max_body_bytes": 10485760, "max_batch_features": 1000, **overrides}}


def test_worldpop_batch_ingestion_publishes_population_and_quota(db, monkeypatch):
    _add_areas(db)
    monkeypatch.setattr(settings, "worldpop_reference_year", 2024)

    def fake_request(method, url, **kwargs):
        if url.endswith("/catalog"):
            return _response(200, url, _catalog([2024, 2025, 2030]))
        if url.endswith("/health"):
            return _response(200, url, _health())
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
        if url.endswith("/health"):
            return _response(200, url, _health())
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


def _batch_answer(body):
    return {"data_year": body["year"], "features": [
        {"id": feature["id"], "total_population": 2000, "population_density": 500,
         "area_km2": 4, "rank": index + 1, "share_of_total": 1 / len(body["features"]["features"])}
        for index, feature in enumerate(body["features"]["features"])], "processing_time_ms": 1}


def test_worldpop_batches_original_boundaries_and_preserves_the_previous_version_on_partial_failure(db, monkeypatch):
    _add_areas(db)
    monkeypatch.setattr(settings, "worldpop_reference_year", 2024)
    requests = []
    should_fail = True

    def fake_request(method, url, **kwargs):
        if url.endswith("/catalog"):
            return _response(200, url, _catalog([2024, 2025]))
        if url.endswith("/health"):
            return _response(200, url, _health(max_vertices=5))
        body = kwargs["json"]
        requests.append(body)
        assert len(body["features"]["features"]) == 1
        if should_fail and body["year"] == 2025 and body["features"]["features"][0]["id"] == "district-b":
            raise httpx.ReadTimeout("Synthetic provider timeout in the second batch")
        return _response(200, url, _batch_answer(body))

    monkeypatch.setattr(httpx, "request", fake_request)
    run_monitor(db, WorldPopConnector(), requested_by="test")
    first = db.scalar(select(DatasetVersion).where(DatasetVersion.status == "published"))
    assert first.quality_summary["request_batches"] == 2
    assert first.quality_summary["geometry_simplified"] is False
    for body in requests:
        feature = body["features"]["features"][0]
        assert feature["geometry"] == db.get(GeographicArea, feature["id"]).geometry
    monkeypatch.setattr(settings, "worldpop_reference_year", 2025)
    with pytest.raises(ConnectorError):
        run_monitor(db, WorldPopConnector(), requested_by="test")
    assert db.scalars(select(PopulationEstimate)).all()[0].reference_year == 2024
    assert len(db.scalars(select(PopulationEstimate)).all()) == 2
    failed = db.scalar(select(DatasetVersion).where(DatasetVersion.status == "failed"))
    failed_id = failed.id
    assert len(db.scalars(select(SourceProcessingCheckpoint)).all()) == 1
    failed.metadata_json = {**failed.metadata_json, "processor_version": "older-failed-processor"}
    db.commit()
    should_fail = False
    run_monitor(db, WorldPopConnector(), requested_by="test-retry")
    assert db.get(DatasetVersion, failed_id).status == "published"
    assert db.get(DatasetVersion, failed_id).metadata_json["processor_version"] == WorldPopConnector.processor_version
    assert len(db.scalars(select(PopulationEstimate)).all()) == 4
    assert sum(body["year"] == 2025 and body["features"]["features"][0]["id"] == "district-a" for body in requests) == 1
    assert not db.scalars(select(SourceProcessingCheckpoint)).all()


@pytest.mark.parametrize("returned_ids", [["district-a", "district-a"], ["district-a"], ["district-a", "unknown-area"]])
def test_worldpop_rejects_duplicate_missing_or_substituted_areas(db, monkeypatch, returned_ids):
    _add_areas(db)
    monkeypatch.setattr(settings, "worldpop_reference_year", 2024)

    def fake_request(method, url, **kwargs):
        if url.endswith("/catalog"):
            return _response(200, url, _catalog([2024]))
        if url.endswith("/health"):
            return _response(200, url, _health())
        answer = _batch_answer(kwargs["json"])
        answer["features"] = [{**answer["features"][0], "id": area_id} for area_id in returned_ids]
        return _response(200, url, answer)

    monkeypatch.setattr(httpx, "request", fake_request)
    with pytest.raises(ConnectorError, match="each requested area exactly once"):
        run_monitor(db, WorldPopConnector(), requested_by="test")
    assert not db.scalars(select(PopulationEstimate)).all()
    assert not db.scalars(select(DatasetVersion).where(DatasetVersion.status == "published")).all()


def test_worldpop_never_simplifies_a_single_area_that_exceeds_provider_limits():
    area = {"id": "too-complex", "geometry": {"type": "Polygon", "coordinates": [[[100.5,13.7],[100.6,13.7],[100.6,13.8],[100.5,13.8],[100.5,13.7]]]}}
    with pytest.raises(ConnectorError, match="boundary was not simplified"):
        WorldPopConnector._population_batches([area], 2024, _health(max_vertices=4)["limits"])


@pytest.mark.parametrize("indicator,value", [("population_estimate", float("nan")), ("population_density", float("inf"))])
def test_worldpop_rejects_nonfinite_provider_values(indicator, value):
    from app.services.connectors.base import AreaProcessingRecord, SourceAsset
    from datetime import datetime, timezone
    connector = WorldPopConnector()
    asset = SourceAsset.with_checksum("unit-test", "synthetic", datetime.now(timezone.utc), {"type": "Polygon"}, metadata={"reference_year": 2024})
    record = AreaProcessingRecord(area_id="unit-test", indicators={"population_estimate": 10, "population_density": 5, indicator: value}, metadata={"reference_year": 2024, "area_km2": 2})
    assert connector.validate_processed(asset, [record])["passed"] is False
