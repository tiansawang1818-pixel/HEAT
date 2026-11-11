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
    monkeypatch.setattr(earth_engine_tiles, "_render_url", lambda spec, version, composite=None: "https://earthengine.googleapis.com/%s/{z}/{x}/{y}" % version.id)
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


def test_current_map_shows_each_districts_latest_clear_value_across_scenes(client, db):
    from datetime import timedelta
    source = db.scalar(select(DataSource).where(DataSource.name == "Sentinel-2"))
    now = datetime.now(timezone.utc)
    shape = lambda x: {"type": "MultiPolygon", "coordinates": [[[[x, 13.7], [x + 0.01, 13.7], [x + 0.01, 13.71], [x, 13.71], [x, 13.7]]]]}
    west = GeographicArea(id="west-district", name_th="ตะวันตก", name_en="West", geometry=shape(100.4))
    east = GeographicArea(id="east-district", name_th="ตะวันออก", name_en="East", geometry=shape(100.6))
    db.add_all([west, east])
    older = DatasetVersion(source_id=source.id, source_asset_id="s2-older", source_version="older", status="published",
                           quality_status="valid", observed_at=now - timedelta(days=6), published_at=now, layer_version=1)
    newer = DatasetVersion(source_id=source.id, source_asset_id="s2-newer", source_version="newer", status="published",
                           quality_status="valid", observed_at=now - timedelta(days=1), published_at=now, layer_version=2)
    db.add_all([older, newer])
    db.flush()
    db.add_all([
        AreaIndicator(area_id=west.id, dataset_version_id=older.id, indicator_key="ndvi", value=0.4, observed_at=older.observed_at),
        AreaIndicator(area_id=east.id, dataset_version_id=newer.id, indicator_key="ndvi", value=0.2, observed_at=newer.observed_at),
    ])
    db.commit()
    current = client.get("/api/map/features", params={"layer": "ndvi"}).json()
    values = {f["properties"]["area_id"]: f["properties"]["value"] for f in current["features"]}
    assert values["west-district"] == 0.4 and values["east-district"] == 0.2
    detail = client.get("/api/areas/west-district").json()
    assert detail["indicators"]["ndvi"]["value"] == 0.4
    pinned = client.get("/api/map/features", params={"layer": "ndvi", "dataset_version_id": newer.id}).json()
    pinned_values = {f["properties"]["area_id"]: f["properties"]["value"] for f in pinned["features"]}
    assert pinned_values["west-district"] is None and pinned_values["east-district"] == 0.2


def test_historical_layer_shows_the_scored_temperature_trend(client, db):
    from app.models.entities import PriorityScore
    area = GeographicArea(id="trend-area", name_th="แนวโน้ม", name_en="Trend",
                          geometry={"type": "MultiPolygon", "coordinates": [[[[100.5, 13.7], [100.51, 13.7], [100.51, 13.71], [100.5, 13.71], [100.5, 13.7]]]]})
    db.add(area)
    db.add(PriorityScore(area_id=area.id, score=60.0, components={"historical_change": 0.42}, weights={},
                         missing_inputs=[], input_versions={}, formula_version="test"))
    db.commit()
    features = client.get("/api/map/features", params={"layer": "historical"}).json()["features"]
    assert next(f for f in features if f["properties"]["area_id"] == "trend-area")["properties"]["value"] == 0.42
