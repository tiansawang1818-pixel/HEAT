from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select

from app.models.entities import AreaIndicator, DataSource, DatasetVersion, GeographicArea, PriorityScore
from app.services.analytics import recalculate_priority_scores
from app.services.versions import INDICATOR_DATASETS, indicator_version_pool

LANDSAT = INDICATOR_DATASETS["lst_c_mean"]
POLYGON = {"type": "Polygon", "coordinates": [[[100.5, 13.7], [100.51, 13.7], [100.51, 13.71], [100.5, 13.71], [100.5, 13.7]]]}


def test_each_district_uses_its_latest_clear_scene_within_the_freshness_window(db):
    now = datetime.now(timezone.utc)
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == LANDSAT))
    for area_id in ("east", "west", "north"):
        db.add(GeographicArea(id=area_id, name_th=area_id, name_en=area_id, geometry=POLYGON, source_name="test"))

    def scene(name, age_days, values):
        version = DatasetVersion(source_id=source.id, source_asset_id=name, source_version=name, checksum=name,
                                 status="published", observed_at=now - timedelta(days=age_days))
        db.add(version)
        db.flush()
        for area_id, value in values.items():
            db.add(AreaIndicator(area_id=area_id, dataset_version_id=version.id, indicator_key="lst_c_mean",
                                 value=value, observed_at=version.observed_at, quality_status="valid"))
        return version

    expired = scene("expired", source.stale_after_seconds / 86400 + 5, {"north": 30.0})
    older = scene("older", 10, {"west": 34.0, "east": 31.0})
    newest = scene("newest", 2, {"east": 36.0})  # clouds hid the west and north
    db.commit()

    pool = indicator_version_pool(db, LANDSAT, now)
    assert newest.id in pool and older.id in pool and expired.id not in pool
    recalculate_priority_scores(db)
    db.commit()

    def latest(area_id):
        return db.scalar(select(PriorityScore).where(PriorityScore.area_id == area_id).order_by(desc(PriorityScore.calculated_at)))

    east, west = latest("east"), latest("west")
    assert east.input_versions["_area_observations"]["lst_c_mean"]["version_id"] == newest.id
    assert west.input_versions["_area_observations"]["lst_c_mean"]["version_id"] == older.id
    assert "temperature_anomaly" not in east.missing_inputs and "temperature_anomaly" not in west.missing_inputs
    north = latest("north")
    assert north is None or "temperature_anomaly" in north.missing_inputs  # only an expired scene covers it


def test_scores_from_the_older_method_are_recalculated_once(db):
    from app.services.analytics import refresh_scores_from_older_method

    now = datetime.now(timezone.utc)
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == LANDSAT))
    db.add(GeographicArea(id="legacy", name_th="legacy", name_en="legacy", geometry=POLYGON, source_name="test"))
    version = DatasetVersion(source_id=source.id, source_asset_id="scene", source_version="1", checksum="c",
                             status="published", observed_at=now - timedelta(days=3))
    db.add(version)
    db.flush()
    db.add(AreaIndicator(area_id="legacy", dataset_version_id=version.id, indicator_key="lst_c_mean",
                         value=35.0, observed_at=version.observed_at, quality_status="valid"))
    db.add(PriorityScore(area_id="legacy", score=50, components={}, weights={}, missing_inputs=[],
                         input_versions={"old": True}, formula_version="priority-v1",
                         calculated_at=now - timedelta(hours=1)))
    db.commit()

    assert refresh_scores_from_older_method(db) == 1
    newest = db.scalar(select(PriorityScore).order_by(desc(PriorityScore.calculated_at)))
    assert newest.input_versions["_area_observations"]["lst_c_mean"]["version_id"] == version.id
    assert refresh_scores_from_older_method(db) == 0  # already on the per-district method
