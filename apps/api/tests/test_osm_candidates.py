import httpx
import pytest
from sqlalchemy import select

from app.api.router import _candidate_rows
from app.models.entities import CandidateIntervention, DatasetVersion, GeographicArea, SourceProcessingCheckpoint
from app.core.config import settings
from app.services.connectors.catalogs import OpenStreetMapConnector
from app.services.connectors.base import ConnectorError
from app.services.pipeline import run_monitor


def _response(status: int, *, text: str = "", json_data=None, headers=None) -> httpx.Response:
    request = httpx.Request("GET", "https://provider.example/data")
    if json_data is not None:
        return httpx.Response(status, json=json_data, headers=headers, request=request)
    return httpx.Response(status, text=text, headers=headers, request=request)


def _add_area(db) -> None:
    db.add(
        GeographicArea(
            id="district-a",
            name_th="เขตทดสอบ",
            name_en="Test district",
            geometry={
                "type": "MultiPolygon",
                "coordinates": [
                    [[
                        [100.4, 13.6],
                        [100.6, 13.6],
                        [100.6, 13.8],
                        [100.4, 13.8],
                        [100.4, 13.6],
                    ]]
                ],
            },
        )
    )
    db.commit()


def _mock_osm(monkeypatch, sequence: int, elements) -> None:
    monkeypatch.setattr(
        "app.services.connectors.catalogs.httpx.get",
        lambda *args, **kwargs: _response(
            200,
            text="sequenceNumber=%s\ntimestamp=2026-11-01T00\\:00\\:00Z\n" % sequence,
        ),
    )
    monkeypatch.setattr(
        "app.services.connectors.catalogs.httpx.post",
        lambda *args, **kwargs: _response(
            200,
            json_data={"generator": "Overpass API", "elements": elements},
        ),
    )


def test_osm_snapshot_publishes_evidence_pending_candidates_and_skips_unchanged(db, monkeypatch):
    _add_area(db)
    elements = [
        {
            "type": "node",
            "id": 101,
            "lat": 13.70,
            "lon": 100.50,
            "tags": {"highway": "bus_stop", "name:th": "ป้ายรถเมล์ทดสอบ"},
        },
        {
            "type": "way",
            "id": 202,
            "center": {"lat": 13.71, "lon": 100.51},
            "tags": {"leisure": "park", "name": "Test Park"},
        },
    ]
    _mock_osm(monkeypatch, 42, elements)

    first = run_monitor(db, OpenStreetMapConnector(), requested_by="test")
    second = run_monitor(db, OpenStreetMapConnector(), requested_by="test")

    candidates = list(db.scalars(select(CandidateIntervention).order_by(CandidateIntervention.id)).all())
    assert first["processed"] == 1
    assert second["processed"] == 0
    assert len(candidates) == 2
    assert all(candidate.estimated_cost_thb is None for candidate in candidates)
    assert all(candidate.feasibility_status == "insufficient_evidence" for candidate in candidates)
    assert all(candidate.active is True for candidate in candidates)
    assert candidates[0].evidence["attribution"] == "© OpenStreetMap contributors"
    assert candidates[0].dataset_versions["osm/bangkok"]["observed_at"]
    assert len(db.scalars(select(DatasetVersion)).all()) == 1
    assert _candidate_rows(db) == []


def test_osm_changed_snapshot_deactivates_removed_candidate_without_deleting_history(db, monkeypatch):
    _add_area(db)
    original = [
        {
            "type": "node",
            "id": 101,
            "lat": 13.70,
            "lon": 100.50,
            "tags": {"highway": "bus_stop"},
        }
    ]
    _mock_osm(monkeypatch, 42, original)
    run_monitor(db, OpenStreetMapConnector(), requested_by="test")

    replacement = [
        {
            "type": "node",
            "id": 303,
            "lat": 13.72,
            "lon": 100.52,
            "tags": {"amenity": "clinic"},
        }
    ]
    _mock_osm(monkeypatch, 43, replacement)
    run_monitor(db, OpenStreetMapConnector(), requested_by="test")

    removed = db.get(CandidateIntervention, "osm-node-101-public-shade-structure")
    added = db.get(CandidateIntervention, "osm-node-303-cooling-rest-area")
    assert removed is not None and removed.active is False
    assert added is not None and added.active is True
    assert len(db.scalars(select(DatasetVersion)).all()) == 2


def test_osm_refresh_preserves_approved_cost_and_feasibility_evidence(db, monkeypatch):
    _add_area(db)
    original = [
        {
            "type": "node",
            "id": 101,
            "lat": 13.70,
            "lon": 100.50,
            "tags": {"amenity": "marketplace", "name": "Old market name"},
        }
    ]
    _mock_osm(monkeypatch, 42, original)
    run_monitor(db, OpenStreetMapConnector(), requested_by="test")
    candidate = db.get(CandidateIntervention, "osm-node-101-public-shade-structure")
    candidate.estimated_cost_thb = 500_000
    candidate.feasibility_status = "eligible"
    candidate.evidence = {
        **candidate.evidence,
        "cost_assumption_id": "cost-v1",
        "cost_evidence": "https://example.org/cost",
        "feasibility_evidence": "https://example.org/site",
        "approved_by": "reviewer",
    }
    db.commit()

    refreshed = [
        {
            "type": "node",
            "id": 101,
            "lat": 13.701,
            "lon": 100.501,
            "tags": {"amenity": "marketplace", "name": "Updated market name"},
        },
        {
            "type": "node",
            "id": 102,
            "lat": 13.71,
            "lon": 100.51,
            "tags": {"amenity": "clinic"},
        },
    ]
    _mock_osm(monkeypatch, 43, refreshed)
    run_monitor(db, OpenStreetMapConnector(), requested_by="test")
    db.refresh(candidate)

    assert candidate.name_th == "Updated market name"
    assert candidate.estimated_cost_thb == 500_000
    assert candidate.feasibility_status == "eligible"
    assert candidate.evidence["cost_assumption_id"] == "cost-v1"
    assert candidate.evidence["approved_by"] == "reviewer"
    assert candidate.evidence["source_tags"]["name"] == "Updated market name"


def test_osm_rate_limit_preserves_retry_after(monkeypatch):
    from app.services.connectors.base import ProviderRateLimitError

    monkeypatch.setattr(
        "app.services.connectors.catalogs.httpx.get",
        lambda *args, **kwargs: _response(429, headers={"Retry-After": "180"}),
    )
    connector = OpenStreetMapConnector()
    try:
        connector.discover()
        assert False, "expected provider rate limit"
    except ProviderRateLimitError as exc:
        assert exc.retry_after_seconds == 180


@pytest.mark.parametrize("status,body", [
    (502, {"error": "synthetic upstream outage"}),
    (200, {"elements": [], "remark": "runtime error: synthetic query timeout; results are incomplete"}),
])
def test_osm_partial_or_failed_queries_cannot_replace_a_validated_snapshot(db, monkeypatch, status, body):
    _add_area(db)
    _mock_osm(monkeypatch, 42, [{"type": "node", "id": 101, "lat": 13.7, "lon": 100.5, "tags": {"amenity": "clinic"}}])
    run_monitor(db, OpenStreetMapConnector(), requested_by="test")
    monkeypatch.setattr("app.services.connectors.catalogs.httpx.post", lambda *args, **kwargs: _response(status, json_data=body))
    with pytest.raises(ConnectorError):
        run_monitor(db, OpenStreetMapConnector(), requested_by="test")
    versions = db.scalars(select(DatasetVersion)).all()
    assert len(versions) == 1 and versions[0].status == "published"
    candidates = db.scalars(select(CandidateIntervention)).all()
    assert len(candidates) == 1 and candidates[0].active is True


@pytest.mark.parametrize("change_replication", [False, True])
def test_osm_resumes_completed_tiles_from_private_storage_only_for_the_same_snapshot(db, monkeypatch, change_replication):
    _add_area(db)
    monkeypatch.setattr(settings, "overpass_grid_divisions", 2)
    _mock_osm(monkeypatch, 42, [])
    queries = []

    def provider(*args, **kwargs):
        query = kwargs["data"]["data"]
        queries.append(query)
        if len(queries) == 3:
            return _response(502, json_data={"error": "synthetic interrupted third tile"})
        return _response(200, json_data={"generator": "Synthetic unit-test Overpass", "elements": [
            {"type": "node", "id": 101, "lat": 13.7, "lon": 100.5, "tags": {"amenity": "clinic"}}]})

    monkeypatch.setattr("app.services.connectors.catalogs.httpx.post", provider)
    with pytest.raises(ConnectorError):
        run_monitor(db, OpenStreetMapConnector(), requested_by="test")
    assert len(db.scalars(select(SourceProcessingCheckpoint)).all()) == 2
    assert not db.scalars(select(CandidateIntervention)).all()
    if change_replication:
        _mock_osm(monkeypatch, 43, [])
        monkeypatch.setattr("app.services.connectors.catalogs.httpx.post", provider)
    run_monitor(db, OpenStreetMapConnector(), requested_by="test-new-worker")
    assert len(queries) == (7 if change_replication else 5)
    version = db.scalar(select(DatasetVersion))
    assert version.status == "published"
    assert version.quality_summary["resumed_tiles"] == (0 if change_replication else 2)
    assert not db.scalars(select(SourceProcessingCheckpoint)).all()


def test_osm_preserves_the_provider_snapshot_date_separately_from_replication_generation(db, monkeypatch):
    _add_area(db)
    monkeypatch.setattr(settings, "overpass_grid_divisions", 1)
    _mock_osm(monkeypatch, 42, [])
    monkeypatch.setattr("app.services.connectors.catalogs.httpx.post", lambda *args, **kwargs: _response(200, json_data={
        "osm3s": {"timestamp_osm_base": "2026-10-31T23:58:00Z"},
        "elements": [{"type": "node", "id": 101, "lat": 13.7, "lon": 100.5, "tags": {"amenity": "clinic"}}],
    }))
    run_monitor(db, OpenStreetMapConnector(), requested_by="test")
    version = db.scalar(select(DatasetVersion))
    assert version.observed_at.isoformat().startswith("2026-10-31T23:58:00")
    assert version.metadata_json["replication_generated_at"] == "2026-11-01T00:00:00Z"
    assert version.metadata_json["observation_time_kind"] == "overpass_snapshot"
