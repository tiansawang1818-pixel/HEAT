import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["DEMO_MODE"] = "false"
os.environ["ADMIN_API_KEY"] = "test-admin-key"

import pytest
from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.main import app
from app.services.bootstrap import ensure_sources


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    ensure_sources(db)
    db.close()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value
