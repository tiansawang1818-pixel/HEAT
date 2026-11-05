import ipaddress
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings


def _is_https(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value)
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _is_production_origin(value: str) -> bool:
    if not _is_https(value) or "*" in value:
        return False
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        if ipaddress.ip_address(hostname).is_loopback:
            return False
    except ValueError:
        pass
    return not parsed.path and not parsed.params and not parsed.query and not parsed.fragment


def _credential_hint() -> bool:
    """Return only whether an explicit Earth Engine credential hint exists.

    Workload identity can intentionally have no local key file, so this is a
    warning signal rather than proof that provider authentication will fail.
    """
    credential_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if credential_path and Path(credential_path).is_file():
        return True
    return bool(os.environ.get("GEE_SERVICE_ACCOUNT", "").strip())


def production_readiness(db: Session, *, probe_dependencies: bool = True) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(
        code: str,
        category: str,
        status: str,
        label_th: str,
        detail_th: str,
        action_th: Optional[str] = None,
    ) -> None:
        item: Dict[str, Any] = {
            "code": code,
            "category": category,
            "status": status,
            "label_th": label_th,
            "detail_th": detail_th,
        }
        if action_th:
            item["action_th"] = action_th
        checks.append(item)

    environment = settings.environment.strip().lower()
    add(
        "runtime.environment",
        "runtime",
        "ready" if environment == "production" else "blocked",
        "โหมดการทำงาน",
        "กำลังทำงานในโหมด production" if environment == "production" else f"โหมดปัจจุบันคือ {environment or 'ไม่ระบุ'}",
        None if environment == "production" else "ตั้ง ENVIRONMENT=production เมื่อผ่านการทดสอบและพร้อมเปิดใช้งานจริง",
    )
    add(
        "runtime.demo_mode",
        "runtime",
        "ready" if not settings.demo_mode else "blocked",
        "ข้อมูลสาธิต",
        "ปิด demo mode แล้ว" if not settings.demo_mode else "backend ยังเปิด demo mode",
        None if not settings.demo_mode else "ตั้ง DEMO_MODE=false และ NEXT_PUBLIC_DEMO_MODE=false",
    )
    add(
        "runtime.fixture_ingestion",
        "runtime",
        "ready" if not settings.allow_fixture_ingestion else "blocked",
        "Fixture ingestion",
        "ปิดการ ingest fixture แล้ว" if not settings.allow_fixture_ingestion else "ยังอนุญาต synthetic fixture ingestion",
        None if not settings.allow_fixture_ingestion else "ตั้ง ALLOW_FIXTURE_INGESTION=false",
    )

    dialect = db.get_bind().dialect.name
    postgis_ready = False
    database_detail = "ฐานข้อมูลไม่พร้อม"
    try:
        db.execute(text("SELECT 1"))
        if dialect == "postgresql":
            version = db.execute(text("SELECT PostGIS_Version()")).scalar_one_or_none()
            postgis_ready = bool(version)
            database_detail = "PostgreSQL และ PostGIS ตอบสนองแล้ว" if postgis_ready else "PostgreSQL ตอบสนอง แต่ไม่พบ PostGIS"
        else:
            database_detail = f"ใช้ {dialect}; เหมาะกับการทดสอบเท่านั้น"
    except Exception:
        database_detail = "ตรวจฐานข้อมูลหรือ PostGIS ไม่สำเร็จ"
    add(
        "storage.postgis",
        "storage",
        "ready" if postgis_ready else "blocked",
        "PostgreSQL / PostGIS",
        database_detail,
        None if postgis_ready else "ใช้ PostgreSQL พร้อม PostGIS extension และรัน migrations",
    )

    redis_configured = settings.redis_url.startswith(("redis://", "rediss://"))
    redis_ready: Optional[bool] = None
    if probe_dependencies and redis_configured:
        try:
            import redis

            with redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.35,
                socket_timeout=0.35,
            ) as client:
                redis_ready = bool(client.ping())
        except Exception:
            redis_ready = False
    if redis_ready is True:
        redis_status, redis_detail = "ready", "Redis durable queue ตอบสนองแล้ว"
    elif redis_ready is False:
        redis_status, redis_detail = "blocked", "เชื่อมต่อ Redis durable queue ไม่สำเร็จ"
    elif redis_configured:
        redis_status, redis_detail = "blocked", "มี Redis configuration แต่ยังไม่ได้ตรวจการเชื่อมต่อ"
    else:
        redis_status, redis_detail = "blocked", "ยังไม่ได้กำหนด Redis URL ที่รองรับ"
    add(
        "queue.redis",
        "queue",
        redis_status,
        "การเชื่อมต่อ Redis",
        redis_detail,
        None if redis_status == "ready" else "ตรวจ Redis, Celery worker และ Celery Beat ให้พร้อมก่อนรับงานจริง",
    )
    add(
        "security.rate_limit",
        "security",
        "ready" if settings.rate_limit_enabled else "blocked",
        "Distributed rate limit",
        "เปิด rate limit ผ่าน Redis แล้ว" if settings.rate_limit_enabled else "ยังปิด rate limit สำหรับงานที่มีต้นทุนสูง",
        None if settings.rate_limit_enabled else "ตั้ง RATE_LIMIT_ENABLED=true หลัง Redis พร้อม",
    )

    oidc_fields_ready = all([settings.oidc_issuer, settings.oidc_audience, settings.oidc_jwks_url])
    oidc_https_ready = _is_https(settings.oidc_issuer) and _is_https(settings.oidc_jwks_url)
    oidc_roles_ready = bool(settings.oidc_roles_claim.strip()) and bool(settings.oidc_admin_roles) and all(
        role.strip() for role in settings.oidc_admin_roles
    )
    oidc_algorithms_ready = bool(settings.oidc_allowed_algorithms) and all(
        algorithm in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
        for algorithm in settings.oidc_allowed_algorithms
    )
    oidc_ready = (
        settings.admin_auth_mode.lower() == "oidc"
        and oidc_fields_ready and oidc_https_ready and oidc_roles_ready and oidc_algorithms_ready
    )
    add(
        "security.oidc",
        "security",
        "ready" if oidc_ready else "blocked",
        "OIDC / RBAC ผู้ดูแล",
        "ตั้งค่า issuer, audience, JWKS และ admin role ครบแล้ว" if oidc_ready else "OIDC production configuration ยังไม่ครบหรือไม่ได้ใช้ HTTPS",
        None if oidc_ready else "ตั้ง ADMIN_AUTH_MODE=oidc พร้อม HTTPS issuer/JWKS, audience และ admin roles",
    )
    cors_ready = bool(settings.cors_origins) and all(_is_production_origin(origin) for origin in settings.cors_origins)
    add(
        "security.cors",
        "security",
        "ready" if cors_ready else "blocked",
        "CORS allowlist",
        "กำหนด HTTPS origin แล้ว" if cors_ready else "allowlist ยังว่างหรือมี origin ที่ใช้จริงไม่ได้",
        None if cors_ready else "กำหนด CORS_ORIGINS เป็น HTTPS origin ที่ใช้งานจริงเท่านั้น",
    )

    gee_project_ready = bool(settings.gee_project)
    add(
        "provider.earth_engine_project",
        "providers",
        "ready" if gee_project_ready else "blocked",
        "Google Earth Engine project",
        "กำหนด project แล้ว" if gee_project_ready else "ยังไม่ได้กำหนด GEE_PROJECT",
        None if gee_project_ready else "กำหนด project ที่เปิด Earth Engine API และมี quota ที่ได้รับอนุมัติ",
    )
    credential_hint = _credential_hint()
    add(
        "provider.earth_engine_auth",
        "providers",
        "ready" if credential_hint else "warning",
        "Earth Engine identity",
        "พบ explicit credential hint" if credential_hint else "ไม่พบ local key hint; workload identity อาจยังใช้ได้",
        "ยืนยัน identity ด้วย source check จริง และใช้ workload identity/secret mount ที่องค์กรอนุมัติ" if not credential_hint else None,
    )
    add(
        "provider.public_sources",
        "providers",
        "warning" if not settings.worldpop_api_key else "ready",
        "WorldPop และ public HTTP providers",
        "กำหนด WorldPop API key แล้ว" if settings.worldpop_api_key else "WorldPop anonymous access มี quota ต่ำกว่าและ Open-Meteo/OSM ต้องเคารพข้อกำหนดผู้ให้บริการ",
        None if settings.worldpop_api_key else "ตรวจ quota, attribution และใช้ provider-approved extract สำหรับ OSM production scale",
    )

    blockers = [item for item in checks if item["status"] == "blocked"]
    warnings = [item for item in checks if item["status"] == "warning"]
    return {
        "status": "ready" if not blockers else "not_ready",
        "configuration_ready": not blockers,
        "scope": "configuration_and_dependencies",
        "limitations_th": "ผลนี้ตรวจการตั้งค่า backend และการเชื่อมต่อ PostGIS/Redis เท่านั้น ต้องยืนยัน provider credentials, worker/scheduler, frontend mode และ ingestion-to-map ด้วยการทดสอบจริงก่อนเปิดใช้งาน",
        "environment": environment,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "ready": sum(1 for item in checks if item["status"] == "ready"),
            "warning": len(warnings),
            "blocked": len(blockers),
        },
        "checks": checks,
        "blockers": [{"code": item["code"], "message_th": item["detail_th"], "action_th": item.get("action_th")} for item in blockers],
        "warnings": [{"code": item["code"], "message_th": item["detail_th"], "action_th": item.get("action_th")} for item in warnings],
        "secrets_exposed": False,
    }
