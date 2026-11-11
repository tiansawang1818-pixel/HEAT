"""Connection exhaustion must backpressure instead of exceeding a managed cap."""
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine as real_create_engine, text
from sqlalchemy.exc import TimeoutError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import session


def test_bounded_pool_times_out_then_reuses_returned_connection(monkeypatch, tmp_path):
    # Exercise the actual configured pool without starting a local DB server.
    # The CI integration suite covers the same factory on real PostgreSQL.
    def isolated_engine(url, **options):
        options.pop("connect_args", None)  # PostgreSQL session options do not apply to SQLite
        return real_create_engine("sqlite+pysqlite:///" + str(tmp_path / "bounded.db"), **options)

    monkeypatch.setattr(session, "create_engine", isolated_engine)
    config = Settings(_env_file=None, database_url="postgresql+psycopg://uncontacted.example/test",
                      database_pool_size=1, database_max_overflow=0, database_pool_timeout_seconds=0.05)
    engine = session.make_database_engine(config)
    try:
        with engine.connect() as first:
            assert first.execute(text("SELECT 1")).scalar() == 1
            with pytest.raises(TimeoutError):
                engine.connect()
        with engine.connect() as second:
            assert second.execute(text("SELECT 2")).scalar() == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize("values", [
    {"database_pool_size": 0},
    {"database_max_overflow": -1},
    {"database_pool_timeout_seconds": 0},
])
def test_pool_cannot_be_configured_as_unbounded(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_memory_sqlite_keeps_schema_between_sessions_with_pool_limits():
    config = Settings(_env_file=None, database_url="sqlite+pysqlite:///:memory:",
                      database_pool_size=1, database_max_overflow=0)
    engine = session.make_database_engine(config)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE retained (value INTEGER)"))
            connection.execute(text("INSERT INTO retained VALUES (7)"))
        with engine.connect() as connection:
            assert connection.execute(text("SELECT value FROM retained")).scalar() == 7
    finally:
        engine.dispose()


def test_connected_sse_client_does_not_starve_a_single_connection_pool(tmp_path):
    import asyncio
    from app.api.router import events_stream
    from app.models.entities import PipelineEvent

    engine = real_create_engine("sqlite+pysqlite:///" + str(tmp_path / "sse.db"),
                                pool_size=1, max_overflow=0, pool_timeout=0.05)
    PipelineEvent.__table__.create(engine)

    class ConnectedRequest:
        headers = {}

        async def is_disconnected(self):
            return False

    async def scenario():
        with Session(engine) as stream_db:
            response = await events_stream(ConnectedRequest(), db=stream_db)
            body = response.body_iterator
            try:
                assert "connected" in await body.__anext__()
                # This independent publisher would time out if SSE retained
                # the only connection after sending its initial frame.
                with Session(engine) as publisher:
                    publisher.add(PipelineEvent(id="pool-release-publication", event_type="layer.published", payload={}))
                    publisher.commit()
                assert "pool-release-publication" in await body.__anext__()
                with engine.connect() as inspector:
                    assert inspector.execute(text("SELECT 1")).scalar() == 1
            finally:
                await body.aclose()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


@pytest.mark.parametrize("phase", ["connect", "poll"])
def test_sse_waiting_for_pool_leaves_event_loop_free_to_release_other_request(tmp_path, phase):
    import asyncio
    from app.api.router import events_stream
    from app.models.entities import PipelineEvent

    engine = real_create_engine("sqlite+pysqlite:///" + str(tmp_path / "sse-contention.db"),
                                pool_size=1, max_overflow=0, pool_timeout=0.3,
                                connect_args={"check_same_thread": False})
    PipelineEvent.__table__.create(engine)

    class ConnectedRequest:
        headers = {}

        async def is_disconnected(self):
            return False

    async def scenario():
        with Session(engine) as stream_db:
            response = await events_stream(ConnectedRequest(), db=stream_db)
            body = response.body_iterator
            if phase == "poll":
                assert "connected" in await body.__anext__()
            holder = engine.connect()
            released = asyncio.Event()

            def release_other_request():
                holder.close()
                released.set()

            scheduled = asyncio.get_running_loop().call_later(0.05, release_other_request)
            try:
                frame = await asyncio.wait_for(body.__anext__(), timeout=1)
                assert released.is_set()
                assert ("connected" if phase == "connect" else "heartbeat") in frame
            finally:
                scheduled.cancel()
                holder.close()
                await body.aclose()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_postgres_connections_cap_work_mem(monkeypatch):
    captured = {}
    monkeypatch.setattr(session, "create_engine", lambda url, **options: captured.update(options))
    monkeypatch.setattr(session.event, "listen", lambda target, name, fn: captured.update(listener=(name, fn)))
    session.make_database_engine(Settings(_env_file=None, database_url="postgresql+psycopg://uncontacted.example/test"))
    options = captured["connect_args"]["options"]
    assert options.startswith("-c work_mem=4MB ") and "-c tcp_keepalives_idle=60" in options
    assert captured["listener"] == ("do_connect", session._retry_when_role_is_full)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_work_mem="4MB; DROP")


def test_connect_waits_out_a_full_role_limit_then_gives_up(monkeypatch):
    from app.db import session as db_session
    monkeypatch.setattr(db_session.time, "sleep", lambda seconds: None)
    attempts = []

    class Dialect:
        def __init__(self, failures):
            self.failures = failures
        def connect(self, *args, **kwargs):
            attempts.append(1)
            if len(attempts) <= self.failures:
                raise RuntimeError('FATAL:  too many connections for role "heatshift_runtime"')
            return "connection"

    assert db_session._retry_when_role_is_full(Dialect(2), None, (), {}) == "connection"
    attempts.clear()
    import pytest
    with pytest.raises(RuntimeError, match="too many connections"):
        db_session._retry_when_role_is_full(Dialect(9), None, (), {})
    assert len(attempts) == 4


def test_role_limit_is_reported_as_busy(client, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.api import router as api_router

    def full(db):
        raise OperationalError("connect", {}, Exception('FATAL:  too many connections for role "heatshift_runtime"'))
    monkeypatch.setattr(api_router, "_dataset_status", full)
    api_router._RESPONSE_CACHE.clear()
    response = client.get("/api/datasets/status")
    assert response.status_code == 503 and response.headers["Retry-After"] == "5"
