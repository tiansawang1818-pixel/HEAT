from contextlib import asynccontextmanager
import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.services.bootstrap import ensure_sources, seed_demo_data
from app.services.observability import request_telemetry
from app.services.rate_limit import rate_limiter


configure_logging(settings.log_level)
http_logger = logging.getLogger("heatshift.http")
request_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _rate_limit_rule(method: str, path: str):
    if method != "POST":
        return None
    exact = {
        "/api/optimization/run": ("optimization", settings.rate_limit_optimization_per_minute, False),
        "/api/scenarios/compare": ("scenario", settings.rate_limit_scenario_per_minute, False),
        "/api/forecast/run": ("forecast", settings.rate_limit_forecast_per_minute, False),
        "/api/assistant/query": ("assistant", settings.rate_limit_assistant_per_minute, False),
        "/api/ingestion/run": ("admin", settings.rate_limit_admin_per_minute, settings.rate_limit_fail_closed_admin),
        "/api/cost-assumptions": ("admin", settings.rate_limit_admin_per_minute, settings.rate_limit_fail_closed_admin),
    }
    if path in exact:
        return exact[path]
    if re.fullmatch(r"/api/candidates/[A-Za-z0-9_-]{1,64}/approve", path):
        return "admin", settings.rate_limit_admin_per_minute, settings.rate_limit_fail_closed_admin
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        ensure_sources(db)
        seed_demo_data(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="HEAT//SHIFT API",
    description="Versioned urban heat data, explainable priority scoring and budget optimization for Bangkok.",
    version="0.1.0",
    lifespan=lifespan,
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Key", "Last-Event-ID"],
)


@app.middleware("http")
async def operational_headers_and_logging(request: Request, call_next):
    candidate = request.headers.get("X-Request-ID", "")
    request_id = candidate if request_id_pattern.fullmatch(candidate) else uuid.uuid4().hex
    started = time.monotonic()
    limit_rule = _rate_limit_rule(request.method, request.url.path)
    decision = None
    try:
        if limit_rule:
            scope, limit, fail_closed = limit_rule
            identifier = request.client.host if request.client else "unknown"
            decision = rate_limiter.check(identifier, scope, limit, 60, fail_closed=fail_closed)
        if decision and not decision.allowed:
            unavailable = not decision.backend_available
            response = JSONResponse(
                status_code=503 if unavailable else 429,
                content={
                    "detail": "Rate-limit service is unavailable" if unavailable else "Request rate limit exceeded",
                },
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
        else:
            response = await call_next(request)
    except Exception:
        duration = time.monotonic() - started
        route = getattr(request.scope.get("route"), "path", "unmatched")
        request_telemetry.observe(request.method, route, 500, duration)
        http_logger.exception(
            "request failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "route": route,
                "status_code": 500,
                "duration_ms": round(duration * 1000, 2),
            },
        )
        raise
    duration = time.monotonic() - started
    route = getattr(request.scope.get("route"), "path", "unmatched")
    request_telemetry.observe(request.method, route, response.status_code, duration)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if decision:
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    if settings.environment.lower() == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    http_logger.info(
        "request completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "route": route,
            "status_code": response.status_code,
            "duration_ms": round(duration * 1000, 2),
        },
    )
    return response


app.include_router(router, prefix=settings.api_prefix)
