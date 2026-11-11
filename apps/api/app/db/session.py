import time
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, settings


def make_database_engine(config: Settings):
    options = {"pool_pre_ping": True, "hide_parameters": True}
    if config.database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if config.database_url.endswith(":memory:"):
            options["poolclass"] = StaticPool
    else:
        # Limits apply per process. Operators must budget all replicas and
        # Celery child processes, leaving provider connections for recovery.
        options.update(
            connect_args={"options": "-c work_mem=%s" % config.database_work_mem},
            pool_size=config.database_pool_size,
            max_overflow=config.database_max_overflow,
            pool_timeout=config.database_pool_timeout_seconds,
        )
    engine = create_engine(config.database_url, **options)
    if not config.database_url.startswith("sqlite"):
        event.listen(engine, "do_connect", _retry_when_role_is_full)
    return engine


def _retry_when_role_is_full(dialect, connection_record, cargs, cparams):
    """Wait briefly when the provider's per-role connection limit is reached.

    During a deploy the old and new containers briefly hold pools at the same
    time and can exceed the managed database's 10-connection role limit; the
    old container's connections close within seconds.
    """
    for attempt in range(4):
        try:
            return dialect.connect(*cargs, **cparams)
        except Exception as exc:
            if "too many connections" not in str(exc) or attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))


engine = make_database_engine(settings)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
