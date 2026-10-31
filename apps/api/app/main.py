from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.services.bootstrap import ensure_sources, seed_demo_data


configure_logging(settings.log_level)


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
app.include_router(router, prefix=settings.api_prefix)
