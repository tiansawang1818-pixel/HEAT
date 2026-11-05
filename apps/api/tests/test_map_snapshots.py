from datetime import datetime, timezone

from sqlalchemy import select

from app.models.entities import AreaIndicator, DataSource, DatasetVersion, GeographicArea, RasterAssetMetadata
from app.services.tiles import earth_engine_tiles
from app.services.versions import current_versions


def _versions(db):
    source = db.scalar(select(DataSource).where(DataSource.name == "Landsat 8/9"))
    area = GeographicArea(id="snapshot-area", name_th="พื้นที่ทดสอบ", name_en="Snapshot test", geometry={"type": "MultiPolygon", "coordinates": [[[[100.5, 13.7], [100.51, 13.7], [100.51, 13.71], [100.5, 13.71], [100.5, 13.7]]]]})
    db.add(area)
    result = []
    for label, observed_month, publish_day, value in [("new", 11, 2, 35.0), ("late-backfill", 10, 3, 37.0)]:
        version = DatasetVersion(source_id=source.id, source_asset_id="snapshot-" + label, source_version=label,
            status="published", quality_status="valid", observed_at=datetime(2026, observed_month, 1, tzinfo=timezone.utc),
            published_at=datetime(2026, 11, publish_day, tzinfo=timezone.utc), layer_version=publish_day)
        db.add(version)
        db.flush()
        db.add_all([
            AreaIndicator(area_id=area.id, dataset_version_id=version.id, indicator_key="lst_c_mean", value=value, observed_at=version.observed_at),
            RasterAssetMetadata(dataset_version_id=version.id, storage_uri="earthengine://" + version.source_asset_id,
                bounds={"type": "Point", "coordinates": [100.5, 13.7]}, band_metadata={}),
        ])
        result.append(version)
    db.add(DatasetVersion(source_id=source.id, source_asset_id="staged-snapshot", source_version="unpublished", status="processing", observed_at=datetime(2026, 11, 4, tzinfo=timezone.utc)))
    db.commit()
    return source, result


def test_latest_and_historical_raster_vector_and_lineage_use_the_same_observation(client, db, monkeypatch):
    source, (new, old) = _versions(db)
    monkeypatch.setattr(earth_engine_tiles, "_render_url", lambda spec, version: "https://earthengine.googleapis.com/%s/{z}/{x}/{y}" % version.id)
    versions = client.get("/api/map/observations?layer=temperature").json()
    assert versions["supports_snapshots"] is True
    assert [item["id"] for item in versions["items"]] == [new.id, old.id]
    assert current_versions(db)[source.dataset_id]["version_id"] == new.id
    for version, expected in [(None, 35.0), (old.id, 37.0)]:
        query = {"dataset_version_id": version} if version else {}
        feature = client.get("/api/map/features", params={"layer": "temperature", **query}).json()
        tile = client.get("/api/map/tiles/temperature", params=query).json()
        expected_id = old.id if version else new.id
        assert feature["metadata"]["dataset_version_id"] == tile["dataset_version_id"] == expected_id
        assert feature["features"][0]["properties"]["value"] == expected
    detail = client.get("/api/areas/snapshot-area", params={"dataset_version_id": old.id}).json()
    assert detail["indicators"]["lst_c_mean"]["value"] == 37.0
    assert detail["priority"] is None
    history = client.get("/api/areas/snapshot-area/history", params={"dataset_version_id": old.id}).json()
    assert [item["value"] for item in history["items"]] == [37.0]


def test_snapshot_ids_cannot_select_staging_or_a_different_layer(client, db):
    _, (new, old) = _versions(db)
    assert client.get("/api/map/features", params={"layer": "ndvi", "dataset_version_id": new.id}).status_code == 404
    staged = db.scalar(select(DatasetVersion).where(DatasetVersion.source_asset_id == "staged-snapshot"))
    assert client.get("/api/map/features", params={"layer": "temperature", "dataset_version_id": staged.id}).status_code == 404
    assert client.get("/api/areas/snapshot-area", params={"dataset_version_id": staged.id}).status_code == 404
    assert client.get("/api/map/observations?layer=priority").json()["supports_snapshots"] is False
