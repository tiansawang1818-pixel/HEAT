import httpx
from sqlalchemy import select

from app.api.router import _candidate_rows
from app.models.entities import CandidateIntervention, DatasetVersion, GeographicArea
from app.services.connectors.catalogs import OpenStreetMapConnector
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
