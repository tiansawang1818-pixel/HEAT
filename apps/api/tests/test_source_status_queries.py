"""Status reads stay bounded as providers and historical versions accumulate."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, select

from app.api.router import datasets
from app.models.entities import DataSource, DatasetVersion, SourceCheck


def test_source_status_batches_history_and_preserves_published_observation(db):
    now = datetime.now(timezone.utc)
    sources = list(db.scalars(select(DataSource).where(DataSource.source_type != "test_fixture")).all())
    for source in sources:
        db.add(DatasetVersion(source_id=source.id, source_asset_id="test-published-old", source_version="old",
                              observed_at=now-timedelta(days=2), discovered_at=now-timedelta(days=2),
                              published_at=now-timedelta(days=2), status="published", quality_status="validated"))
        db.add(DatasetVersion(source_id=source.id, source_asset_id="test-published-new", source_version="new",
                              observed_at=now-timedelta(minutes=30), discovered_at=now-timedelta(minutes=25),
                              published_at=now-timedelta(minutes=20), status="published", quality_status="validated",
                              layer_version=2))
        db.add(DatasetVersion(source_id=source.id, source_asset_id="test-rejected", source_version="rejected",
                              observed_at=now, discovered_at=now-timedelta(minutes=5), status="failed"))
        db.add(SourceCheck(source_id=source.id, checked_at=now-timedelta(minutes=3), status="completed"))
        db.add(SourceCheck(source_id=source.id, checked_at=now-timedelta(minutes=1), status="failed"))
    db.commit()
    selects = []

    def record_select(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record_select)
    try:
        result = datasets(db)
    finally:
        event.remove(engine, "before_cursor_execute", record_select)
    assert result["count"] == len(sources) and len(sources) > 1
    assert len(selects) <= 4
    for item in result["items"]:
        assert item["status"] == "error"
        assert item["source_version"] == "new" and item["layer_version"] == 2
        assert item["quality_status"] == "validated"
        assert datetime.fromisoformat(item["last_source_observation"]) == now-timedelta(minutes=30)
        assert datetime.fromisoformat(item["last_system_check"]) == now-timedelta(minutes=1)


def test_cloud_obscured_scene_keeps_satellite_source_current(db):
    now = datetime.now(timezone.utc)
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == "GOOGLE/DYNAMICWORLD/V1"))
    db.add(DatasetVersion(source_id=source.id, source_asset_id="clear", source_version="clear",
                          observed_at=now-timedelta(days=3), discovered_at=now-timedelta(days=2),
                          published_at=now-timedelta(days=2), status="published", quality_status="valid"))
    db.add(DatasetVersion(source_id=source.id, source_asset_id="cloudy", source_version="cloudy",
                          observed_at=now-timedelta(days=1), discovered_at=now-timedelta(hours=1),
                          status="failed", quality_status="rejected", quality_summary={"processed_areas": 0}))
    db.commit()
    item = next(row for row in datasets(db)["items"] if row["dataset_id"] == "GOOGLE/DYNAMICWORLD/V1")
    assert item["status"] == "up_to_date" and item["source_version"] == "clear"
    assert "เมฆ" in item["status_reason"]


def test_bootstrap_syncs_code_owned_freshness_window(db):
    from app.services.bootstrap import ensure_sources
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == "GOOGLE/DYNAMICWORLD/V1"))
    source.stale_after_seconds = 21 * 86400
    db.commit()
    ensure_sources(db)
    db.refresh(source)
    assert source.stale_after_seconds == 365 * 86400
