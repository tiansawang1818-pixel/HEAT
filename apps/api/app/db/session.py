from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, settings


def make_database_engine(config: Settings):
    options = {"pool_pre_ping": True}
    if config.database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if config.database_url.endswith(":memory:"):
            options["poolclass"] = StaticPool
    else:
        # Limits apply per process. Operators must budget all replicas and
        # Celery child processes, leaving provider connections for recovery.
        options.update(
            pool_size=config.database_pool_size,
            max_overflow=config.database_max_overflow,
            pool_timeout=config.database_pool_timeout_seconds,
        )
    return create_engine(config.database_url, **options)


engine = make_database_engine(settings)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
