from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.entities import DataSource, DatasetVersion, RasterAssetMetadata
from app.services.connectors.base import ConnectorError
from app.services.tiles import EarthEngineTileService, earth_engine_tiles


def _published_raster(db):
    source = db.scalar(
        select(DataSource).where(
            DataSource.dataset_id == "LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2"
        )
    )
    version = DatasetVersion(
        source_id=source.id,
        source_asset_id="LANDSAT/LC09/C02/T1_L2/LC09_TEST",
        source_version="LC09_TEST",
        checksum="source-checksum",
        observed_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        published_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        status="published",
        quality_status="valid",
        layer_version=3,
        geographic_extent={"type": "Point", "coordinates": [100.5, 13.7]},
    )
    db.add(version)
    db.flush()
    db.add(
        RasterAssetMetadata(
            dataset_version_id=version.id,
            storage_uri="earthengine://LANDSAT/LC09/C02/T1_L2/LC09_TEST",
            media_type="application/vnd.google-earth-engine.image",
            bounds={"type": "Point", "coordinates": [100.5, 13.7]},
            resolution_m=30,
            band_metadata={"processor_version": "gee-zonal-v1"},
        )
    )
    db.commit()
    return version


def test_tile_descriptor_uses_latest_validated_raster_and_caches_map_id(db, monkeypatch):
    version = _published_raster(db)
    service = EarthEngineTileService()
    calls = []

    def render(spec, selected_version):
        calls.append((spec.band, selected_version.id))
        return "https://earthengine.googleapis.com/v1/maps/test/tiles/{z}/{x}/{y}"

    monkeypatch.setattr(service, "_render_url", render)
    first = service.describe(db, "temperature")
    second = service.describe(db, "temperature")

    assert first["available"] is True
    assert first["dataset_version_id"] == version.id
    assert first["source_asset_id"] == version.source_asset_id
    assert first["layer_version"] == 3
    assert first["legend"]["unit"] == "°C (Land Surface Temperature)"
    assert second["tiles"] == first["tiles"]
    assert calls == [("lst_c", version.id)]


def test_tile_descriptor_endpoint_returns_private_cache_metadata(client, db, monkeypatch):
    version = _published_raster(db)
    monkeypatch.setattr(
        earth_engine_tiles,
        "_render_url",
        lambda spec, selected_version: "https://earthengine.googleapis.com/v1/maps/test/tiles/{z}/{x}/{y}",
    )

    response = client.get("/api/map/tiles/temperature")

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["dataset_version_id"] == version.id
    assert response.headers["cache-control"] == "private, max-age=300"


def test_tile_descriptor_is_unavailable_without_published_raster(client):
    response = client.get("/api/map/tiles/ndvi")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.headers["cache-control"] == "no-store"


def test_tile_url_rejects_unexpected_hosts_and_missing_xyz_placeholders():
    with pytest.raises(ConnectorError, match="unexpected tile host"):
        EarthEngineTileService._validated_tile_url("https://example.org/{z}/{x}/{y}")
    with pytest.raises(ConnectorError, match="XYZ placeholders"):
        EarthEngineTileService._validated_tile_url("https://earthengine.googleapis.com/static.png")


def test_current_tiles_mosaic_recent_scenes_newest_on_top(db, monkeypatch):
    from datetime import timedelta
    older = _published_raster(db)
    newer = DatasetVersion(source_id=older.source_id, source_asset_id="LANDSAT/LC09/C02/T1_L2/LC09_NEWER",
                           source_version="newer", status="published", quality_status="valid", layer_version=4,
                           observed_at=older.observed_at + timedelta(days=8), published_at=older.published_at)
    db.add(newer)
    db.flush()
    db.add(RasterAssetMetadata(dataset_version_id=newer.id, storage_uri="earthengine://x", media_type="application/vnd.google-earth-engine.image",
                               bounds={"type": "Point", "coordinates": [100.5, 13.7]}, resolution_m=30, band_metadata={}))
    db.commit()
    service = EarthEngineTileService()
    seen = []
    monkeypatch.setattr(service, "_render_url", lambda spec, version, composite=None: seen.append(composite) or
                        "https://earthengine.googleapis.com/v1/maps/test/tiles/{z}/{x}/{y}")
    current = service.describe(db, "temperature")
    assert seen[-1] == [older.source_asset_id, newer.source_asset_id]
    assert current["composite_scene_count"] == 2 and current["dataset_version_id"] == newer.id
    service.describe(db, "temperature", older.id)
    assert seen[-1] is None
