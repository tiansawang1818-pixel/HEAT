from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import IngestionJob
from app.schemas.api import BackfillBudget, BackfillRequest
from app.services.historical import (
    JOB_TYPE, BackfillUnavailable, cancel_backfill, create_backfill, require_enabled, resume_backfill,
)
from app.services.locks import LockBackendUnavailable


router = APIRouter(prefix="/ingestion/backfills", tags=["Historical ingestion"])


def summary(job):
    checkpoint = job.checkpoint or {}
    return {
        "id": job.id, "status": job.status, "stage": job.stage, "progress": job.progress,
        "configuration": checkpoint.get("configuration", {}), "cursor": checkpoint.get("cursor", {}),
        "catalogue_requests": checkpoint.get("catalogue_requests", 0),
        "processing_attempts": checkpoint.get("processing_attempts", 0),
        "pending_assets": len(checkpoint.get("pending", [])), "skipped": checkpoint.get("skipped", 0),
        "scanned": checkpoint.get("scanned", 0), "next_step_at": checkpoint.get("next_step_at"),
        "resumed_from_job_id": checkpoint.get("resumed_from_job_id"),
        "created_at": job.created_at.isoformat(), "error_message": job.error_message,
        "can_resume": job.status not in ("queued", "running", "cancel_requested") and job.stage != "completed",
    }


def durable_queue():
    try:
        require_enabled()
    except BackfillUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        import redis
        with redis.Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3) as client:
            client.ping()
    except Exception as exc:
        raise HTTPException(503, "คิวงานถาวรยังไม่พร้อม งานย้อนหลังจะไม่ทำงานใน HTTP process") from exc


@router.get("")
def backfill_status(db: Session = Depends(get_db)):
    jobs = db.scalars(select(IngestionJob).where(IngestionJob.job_type == JOB_TYPE)
                      .order_by(IngestionJob.created_at.desc()).limit(20)).all()
    return {
        "enabled": settings.historical_ingestion_enabled and bool(settings.gee_project),
        "reason": "ต้องตรวจสิทธิ์และโควตา Earth Engine ก่อนเปิดการนำเข้าย้อนหลัง ไม่รับรองว่าจะได้ข้อมูลผ่านเมฆทุกช่วงเวลา",
        "budget_note": "เพดาน asset นับทุกครั้งที่พยายามประมวลผล รวมการลองใหม่ ไม่ใช่เพดาน EECU หรือค่าใช้จ่ายของผู้ให้บริการ",
        "items": [summary(job) for job in jobs],
    }


def response_for(job):
    return {"job_id": job.id, "status": job.status, "status_url": "/api/ingestion/status?job_id=%s" % job.id,
            "dispatch_mode": "celery-durable-dispatcher"}


@router.post("", status_code=202)
def start_backfill(request: BackfillRequest, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    durable_queue()
    try:
        return response_for(create_backfill(db, request, "admin:%s" % actor))
    except BackfillUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except LockBackendUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/{job_id}/resume", status_code=202)
def resume(job_id: str, request: BackfillBudget, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    durable_queue()
    try:
        return response_for(resume_backfill(db, job_id, request, "admin:%s" % actor))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except BackfillUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except LockBackendUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/{job_id}/cancel")
def cancel(job_id: str, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return summary(cancel_backfill(db, job_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
