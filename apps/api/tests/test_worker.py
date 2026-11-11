from unittest.mock import MagicMock

import pytest
from celery.exceptions import Retry

from app import worker
from app.models.entities import IngestionJob
from app.services.connectors.base import ConnectorError, ProviderRateLimitError


def _run(task, job_id, retries=0):
    task.push_request(id="worker-test-task", retries=retries, called_directly=False)
    try:
        return task.run(job_id, "open-meteo")
    finally:
        task.pop_request()


def test_manual_worker_retries_rate_limits_and_redelivery_is_idempotent(db, monkeypatch):
    job = IngestionJob(job_type="manual_refresh", status="queued", stage="queued")
    db.add(job)
    db.commit()
    job_id = job.id
    monitor = MagicMock(side_effect=[ProviderRateLimitError("quota exhausted", retry_after_seconds=180), {"status": "success", "processed": 1}])
    retry = MagicMock(side_effect=Retry("retry scheduled"))
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker, "run_monitor", monitor)
    monkeypatch.setattr(worker.run_manual_ingestion, "retry", retry)

    with pytest.raises(Retry):
        _run(worker.run_manual_ingestion, job_id)
    waiting = db.get(IngestionJob, job_id)
    assert waiting.status == "queued"
    assert waiting.stage == "retry_scheduled"
    assert waiting.attempts == 1
    assert waiting.checkpoint["retry_after_seconds"] == 180
    assert waiting.checkpoint["next_retry_at"]
    assert waiting.finished_at is None
    assert retry.call_args.kwargs["countdown"] == 180

    _run(worker.run_manual_ingestion, job_id, retries=1)
    completed = db.get(IngestionJob, job_id)
    assert completed.status == "completed"
    assert completed.attempts == 2
    assert completed.error_message is None
    assert completed.checkpoint["processed"] == 1
    _run(worker.run_manual_ingestion, job_id)
    assert monitor.call_count == 2


def test_manual_worker_retries_lock_contention_instead_of_claiming_completion(db, monkeypatch):
    job = IngestionJob(job_type="manual_refresh", status="queued", stage="queued")
    db.add(job)
    db.commit()
    job_id = job.id
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker, "run_monitor", lambda *args, **kwargs: {"status": "locked"})
    retry = MagicMock(side_effect=Retry("retry scheduled"))
    monkeypatch.setattr(worker.run_manual_ingestion, "retry", retry)
    with pytest.raises(Retry):
        _run(worker.run_manual_ingestion, job_id, retries=2)
    assert db.get(IngestionJob, job_id).stage == "retry_scheduled"
    assert 120 <= retry.call_args.kwargs["countdown"] <= 135


def test_manual_worker_marks_terminal_failure_after_retry_budget(db, monkeypatch):
    job = IngestionJob(job_type="manual_refresh", status="queued", stage="queued")
    db.add(job)
    db.commit()
    job_id = job.id
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker, "run_monitor", MagicMock(side_effect=ConnectorError("provider offline")))
    retry = MagicMock()
    monkeypatch.setattr(worker.run_manual_ingestion, "retry", retry)
    # An unreachable provider is an expected outcome: recorded, not raised as an app error.
    result = _run(worker.run_manual_ingestion, job_id, retries=5)
    assert result == {"status": "failed", "job_id": job_id, "error": "provider offline"}
    failed = db.get(IngestionJob, job_id)
    assert failed.status == "failed"
    assert failed.attempts == 6
    assert failed.finished_at is not None
    retry.assert_not_called()


def test_dispatcher_skips_tick_quietly_when_redis_stalls(monkeypatch):
    def stalled():
        raise TimeoutError("Timeout reading from socket")
    monkeypatch.setattr(worker, "_dispatch_ingestion", stalled)
    assert worker.dispatch_ingestion.run() == {"status": "skipped", "reason": "redis_unavailable"}


def test_orphan_sweep_is_a_no_op_outside_postgres(db):
    assert worker.close_orphaned_sessions(db) == 0
