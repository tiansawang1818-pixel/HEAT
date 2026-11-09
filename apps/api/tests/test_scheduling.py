from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import func, select

from app.models.entities import DataSource, IngestionJob, SourceCheck
from app.services.scheduling import dispatch_due_sources
from app.services.locks import MONITOR_LOCK_LEASE_SECONDS


def _weather_only(db):
    for source in db.scalars(select(DataSource)):
        source.enabled = source.source_type == "weather"
    db.commit()
    return db.scalar(select(DataSource).where(DataSource.source_type == "weather"))


def _queue():
    return MagicMock(return_value=SimpleNamespace(id="ci-durable-task"))


def test_new_install_dispatches_boundaries_and_weather_without_waiting_days(db):
    queue = _queue()
    result = dispatch_due_sources(db, queue)
    assert {row["source"] for row in result["dispatched"]} == {"bma-boundaries", "open-meteo"}
    assert len(result["deferred"]) == 5
    assert all(row["reason"] == "waiting_for_validated_boundaries" for row in result["deferred"])
    assert dispatch_due_sources(db, queue)["dispatched"] == []
    assert queue.call_count == 2
    assert db.scalar(select(func.count()).select_from(IngestionJob)) == 2


def test_restart_uses_durable_last_check_instead_of_resetting_provider_cadence(db):
    source = _weather_only(db)
    now = datetime.now(timezone.utc)
    db.add(SourceCheck(source_id=source.id, checked_at=now - timedelta(minutes=59), status="success"))
    db.commit()
    queue = _queue()
    assert dispatch_due_sources(db, queue, now=now)["dispatched"] == []
    result = dispatch_due_sources(db, queue, now=now + timedelta(minutes=2))
    assert len(result["dispatched"]) == 1
    assert queue.call_count == 1


def test_broker_failure_is_durable_and_has_a_cooldown_without_leaking_errors(db):
    _weather_only(db)
    now = datetime.now(timezone.utc)
    queue = MagicMock(side_effect=ConnectionError("redis://private:secret@unavailable"))
    assert dispatch_due_sources(db, queue, now=now)["deferred"][0]["reason"] == "queue_unavailable"
    job = db.scalar(select(IngestionJob))
    assert job.stage == "dispatch_failed" and job.status == "failed"
    assert "private" not in job.error_message and "secret" not in job.error_message
    assert dispatch_due_sources(db, queue, now=now + timedelta(seconds=30))["dispatched"] == []
    assert queue.call_count == 1


def test_crash_between_committing_and_dispatching_replays_the_same_job(db):
    source = _weather_only(db)
    now = datetime.now(timezone.utc)
    job = IngestionJob(source_id=source.id, job_type="scheduled_refresh", stage="dispatch_pending", status="queued", created_at=now - timedelta(minutes=3))
    db.add(job); db.commit()
    queue = _queue()
    result = dispatch_due_sources(db, queue, now=now)
    assert result["dispatched"] == [{"source": "open-meteo", "job_id": job.id}]
    assert db.scalar(select(func.count()).select_from(IngestionJob)) == 1


def test_queued_retry_is_not_replayed_before_retry_time_and_execution_lease(db):
    source = _weather_only(db)
    now = datetime.now(timezone.utc)
    job = IngestionJob(source_id=source.id, job_type="scheduled_refresh", stage="retry_scheduled", status="queued", created_at=now - timedelta(hours=2), checkpoint={"next_retry_at": (now + timedelta(minutes=5)).isoformat()})
    db.add(job); db.commit()
    queue = _queue()
    assert dispatch_due_sources(db, queue, now=now)["dispatched"] == []
    later = now + timedelta(minutes=5, seconds=MONITOR_LOCK_LEASE_SECONDS + 61)
    assert dispatch_due_sources(db, queue, now=later)["dispatched"][0]["job_id"] == job.id


def _boundaries_only(db):
    for source in db.scalars(select(DataSource)):
        source.enabled = source.source_type == "boundary"
    db.commit()
    return db.scalar(select(DataSource).where(DataSource.source_type == "boundary"))


def test_unsuccessful_or_never_published_sources_retry_hourly_not_weekly(db):
    from app.models.entities import DatasetVersion

    source = _boundaries_only(db)
    now = datetime.now(timezone.utc)
    check = SourceCheck(source_id=source.id, checked_at=now - timedelta(minutes=61), status="degraded")
    db.add(check)
    db.commit()
    queue = _queue()
    assert len(dispatch_due_sources(db, queue, now=now)["dispatched"]) == 1

    # A successful check with a published version waits for the weekly cadence.
    db.query(IngestionJob).delete()
    check.status = "success"
    db.add(DatasetVersion(source_id=source.id, source_asset_id="bma-snapshot", source_version="1",
                          checksum="x", status="published"))
    db.commit()
    assert dispatch_due_sources(db, _queue(), now=now)["dispatched"] == []
