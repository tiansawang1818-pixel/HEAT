from app.core.config import settings
from app.services.geometry import to_db_geometry, to_geojson


def test_postgis_geometry_round_trip_uses_geojson_at_api_boundary(monkeypatch):
    polygon = {
        "type": "MultiPolygon",
        "coordinates": [[[[100.5, 13.7], [100.6, 13.7], [100.6, 13.8], [100.5, 13.8], [100.5, 13.7]]]],
    }
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://test")

    database_value = to_db_geometry(polygon)
    restored = to_geojson(database_value)

    assert restored["type"] == "MultiPolygon"
    assert restored["coordinates"] == polygon["coordinates"]
