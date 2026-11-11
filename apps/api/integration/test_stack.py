"""Real PostGIS + Redis + Celery integration against an isolated CI database.

Synthetic fixtures remain explicitly labelled and never contact real providers.
Run only with HEATSHIFT_RUN_STACK_TESTS=true and a database named heatshift_ci.
"""
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import redis
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker


if os.environ.get("HEATSHIFT_RUN_STACK_TESTS") != "true":
    pytest.skip("Real service integration requires an explicitly isolated CI stack", allow_module_level=True)

from app.core.config import settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.entities import AreaIndicator, DataSource, DatasetVersion, DistrictBoundary, GeographicArea, PipelineEvent
from app.services.checkpoints import SourceCheckpointStore, fingerprint
from app.services.bootstrap import ensure_sources
from app.services.geometry import to_db_geometry
from app.services.locks import MONITOR_HARD_TIME_LIMIT_SECONDS, distributed_lock


@pytest.fixture
def isolated_history_database():
    # Separate from the publication fixture's intentionally empty database.
    assert engine.url.database == "heatshift_ci" and engine.url.host in {"localhost", "127.0.0.1"}
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("CREATE DATABASE heatshift_history_ci"))
    history_engine = create_engine(engine.url.set(database="heatshift_history_ci"))
    try:
        with history_engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION postgis"))
        Base.metadata.create_all(history_engine)
        yield sessionmaker(bind=history_engine, expire_on_commit=False)
    finally:
        history_engine.dispose()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("DROP DATABASE heatshift_history_ci"))


def test_historical_checkpoints_survive_sessions_with_real_postgis_and_redis(monkeypatch, isolated_history_database):
    from app.schemas.api import BackfillBudget, BackfillRequest
    from app.services.connectors.base import AreaProcessingRecord, SourceAsset
    from app.services.connectors.earth_engine import HistoricalPage, LandsatConnector
    from app.services.historical import create_backfill, execute_backfill_step, resume_backfill
    from app.services.pipeline import run_monitor
    from app.services.versions import published_version

    class SyntheticHistory(LandsatConnector):
        produces_raster = False
        catalogue_calls = 0

        def image(self, year):
            return SourceAsset.with_checksum("synthetic-ci-history-%s" % year, "synthetic-only",
                datetime(year, 1, 2, tzinfo=timezone.utc), {"type": "Point", "coordinates": [100.5, 13.7]},
                metadata={"fixture": True, "scientific_use": False})

        def discover(self, latest_asset_id=None, checkpoint=None):
            return [self.image(2024)]

        def discover_historical_page(self, start_date, end_date, cursor, page_size=10, window_days=16, months=None):
            self.catalogue_calls += 1
            return HistoricalPage([self.image(2018), self.image(2019)],
                                  {"window_start": end_date.isoformat(), "collection_index": 0}, 2, True)

        def process(self, asset, areas):
            return [AreaProcessingRecord(area["id"], {"lst_c_mean": 35, "lst_c_p90": 39},
                coverage_fraction=.8, metadata={"fixture": True, "scientific_use": False}) for area in areas]

    monkeypatch.setattr(settings, "historical_ingestion_enabled", True)
    monkeypatch.setattr(settings, "gee_project", "synthetic-ci-not-a-real-provider-project")
    connector = SyntheticHistory()
    with isolated_history_database() as db:
        ensure_sources(db)
        db.add(GeographicArea(id="ci-history", name_th="พื้นที่สังเคราะห์ทดสอบย้อนหลัง", name_en="Synthetic history",
            geometry=to_db_geometry(_polygon(100.5, 13.7)), source_name="Synthetic CI fixture", quality_status="synthetic_demo"))
        db.commit()
        run_monitor(db, connector, requested_by="isolated-history-test")
        active_id = published_version(db, connector.dataset_id).id
        job = create_backfill(db, BackfillRequest(source="landsat", start_date="2018-01-01", end_date="2020-01-01", max_assets=1), "ci-test")
        job_id = job.id
        assert execute_backfill_step(db, job_id, 0, connector)["status"] == "budget_exhausted"
    # A new process/session can recover entirely from committed JSON; no in-memory cursor is required.
    with isolated_history_database() as db:
        resumed = resume_backfill(db, job_id, BackfillBudget(max_assets=2), "ci-test")
        assert execute_backfill_step(db, resumed.id, 0, connector)["status"] == "completed"
        assert connector.catalogue_calls == 1
        assert published_version(db, connector.dataset_id).id == active_id
        assert db.scalar(select(func.count()).select_from(DatasetVersion).where(
            DatasetVersion.source_asset_id.like("synthetic-ci-history-%"), DatasetVersion.status == "published")) == 3


@pytest.fixture(scope="module", autouse=True)
def isolated_stack_guard():
    assert engine.url.database == "heatshift_ci", "Refusing to test against a non-CI database"
    assert engine.url.host in {"localhost", "127.0.0.1"}
    assert settings.environment.lower() != "production"


def _polygon(lon, lat):
    return {"type": "MultiPolygon", "coordinates": [[[[lon, lat], [lon + .01, lat], [lon + .01, lat + .01], [lon, lat + .01], [lon, lat]]]]}


def test_first_real_boundary_publication_respects_postgis_foreign_keys(monkeypatch, isolated_history_database):
    from app.services.connectors.catalogs import BangkokBoundaryConnector
    from app.services.pipeline import run_monitor

    def unreachable(*args, **kwargs):
        raise httpx.ConnectTimeout("isolated test does not contact BMA")

    monkeypatch.setattr(httpx, "get", unreachable)
    with isolated_history_database() as db:
        ensure_sources(db)
        result = run_monitor(db, BangkokBoundaryConnector(), requested_by="ci-boundary-bootstrap")
        assert result["status"] == "degraded" and result["processed"] == 1
        assert db.scalar(select(func.count()).select_from(GeographicArea)) == 50
        assert db.scalar(select(func.count()).select_from(DistrictBoundary)) == 50
        assert db.scalar(select(func.count()).select_from(DistrictBoundary).where(
            func.ST_IsValid(DistrictBoundary.geometry), func.ST_SRID(DistrictBoundary.geometry) == 4326)) == 50


def test_real_postgis_migrations_and_redis_lock_ownership():
    with SessionLocal() as db:
        assert db.execute(text("SELECT PostGIS_Version()")).scalar_one()
        expected_head = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "migrations")).get_current_head()
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == expected_head
        index = db.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_geographic_areas_geometry_gist'")).scalar_one()
        assert "gist" in index.lower()
        ensure_sources(db)
        source = db.scalar(select(DataSource).where(DataSource.dataset_id == "open-meteo/weather-context"))
        source_id = source.id
        scope, chunk = fingerprint("synthetic-ci-scope"), fingerprint("synthetic-ci-chunk")
        store = SourceCheckpointStore(db, source_id, "ci-checkpoint-test")
        store.put(scope, chunk, {"synthetic_staging": True}, 60)
        with SessionLocal() as another:
            assert SourceCheckpointStore(another, source_id, "ci-checkpoint-test").get(scope, chunk) == {"synthetic_staging": True}
        store.clear()
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=2)
    try:
        assert client.ping()
        key = "ci-exclusive:%s" % uuid4().hex
        with distributed_lock(key) as acquired:
            assert acquired is True
            assert client.ttl("heatshift:%s" % key) > MONITOR_HARD_TIME_LIMIT_SECONDS
            with distributed_lock(key) as duplicate:
                assert duplicate is False
        assert not client.exists("heatshift:%s" % key)
    finally:
        client.close()


@pytest.fixture
def running_stack(tmp_path):
    api_root = Path(__file__).resolve().parents[1]
    with SessionLocal() as db:
        ensure_sources(db)
        for area_id, lon, lat in [("ci-central", 100.50, 13.70), ("ci-outside-viewport", 100.85, 13.90)]:
            db.add(GeographicArea(
                id=area_id, name_th="พื้นที่สังเคราะห์ทดสอบระบบ", name_en=area_id,
                geometry=to_db_geometry(_polygon(lon, lat)),
                centroid=to_db_geometry({"type": "Point", "coordinates": [lon + .005, lat + .005]}),
                source_name="Synthetic integration fixture", quality_status="synthetic_demo",
            ))
        db.commit()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {
        **os.environ, "DEMO_MODE": "false", "LOG_LEVEL": "WARNING",
        "ALLOW_FIXTURE_INGESTION": "true", "FIXTURE_ROOT": str(tmp_path),
        "ADMIN_AUTH_MODE": "local_key", "ADMIN_API_KEY": "isolated-ci-operator",
    }
    commands = [
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--log-level", "warning"],
        [sys.executable, "-m", "celery", "-A", "app.worker:celery_app", "worker", "--pool=solo", "--concurrency=1", "--loglevel=WARNING", "--without-gossip", "--without-mingle", "--without-heartbeat"],
    ]
    processes = []
    with ExitStack() as resources:
        for index, command in enumerate(commands):
            log = resources.enter_context((tmp_path / ("process-%s.log" % index)).open("w"))
            processes.append(subprocess.Popen(command, cwd=api_root, env=env, stdout=log, stderr=subprocess.STDOUT))
        client = resources.enter_context(httpx.Client(base_url="http://127.0.0.1:%s" % port, timeout=20, trust_env=False))
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                assert all(process.poll() is None for process in processes), "API or worker exited during startup"
                try:
                    if client.get("/api/health").is_success:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.2)
            else:
                pytest.fail("The real API did not start within 30 seconds")
            yield client, tmp_path
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for path in tmp_path.glob("process-*.log"):
                print(path.name, path.read_text()[-5_000:])


def _refresh(client, path):
    response = client.post("/api/ingestion/run", json={"source": "fixture", "fixture_path": str(path)}, headers={"X-Admin-Key": "isolated-ci-operator"})
    assert response.status_code == 202, response.text
    assert response.json()["dispatch_mode"] == "celery-durable-queue"
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get("/api/ingestion/status", params={"job_id": job_id}).json()["job"]
        assert job["status"] != "failed", job
        if job["status"] == "completed":
            return job
        time.sleep(.2)
    pytest.fail("The durable Celery job did not finish within 30 seconds")


def _publication(lines):
    deadline = time.monotonic() + 20
    for line in lines:
        assert time.monotonic() < deadline, "No publication arrived on the live SSE stream within 20 seconds"
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            if payload.get("type") == "layer.published":
                return payload
    pytest.fail("SSE ended before delivering a publication")


def test_authorized_queue_to_postgis_map_and_continuous_sse(running_stack):
    client, fixture_root = running_stack
    observed_at = datetime.now(timezone.utc) - timedelta(days=2)
    path = fixture_root / "provider-update.json"

    def fixture(asset_id, value, observed):
        path.write_text(json.dumps({
            "fixture_kind": "synthetic", "publication_allowed": False,
            "source_asset_id": asset_id, "source_version": asset_id, "observed_at": observed.isoformat(),
            "payload": {"area_indicators": [{"area_id": "ci-central", "indicator_key": "lst_c_mean", "value": value, "unit": "°C"}]},
        }))

    fixture("ci-synthetic-v1", 38.4, observed_at)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PipelineEvent)) == 0
    # Hold one real HTTP stream across both publications; the first connects to an empty event history.
    with client.stream("GET", "/api/events/stream") as stream:
        assert stream.status_code == 200
        assert "no-transform" in stream.headers["Cache-Control"]
        lines = stream.iter_lines()
        assert next(lines) == "retry: 5000"
        assert next(lines) == ": connected"
        assert _refresh(client, path)["checkpoint"]["processed"] == 1
        first_event = _publication(lines)
        assert first_event["layer_version"] == 1
        assert "temperature" in first_event["affected_layers"]
        query = {"layer": "temperature", "bbox": "100.49,13.69,100.53,13.73"}
        before = client.get("/api/map/features", params=query)
        assert before.status_code == 200
        assert before.json()["metadata"]["spatial_filter"] == "postgis_st_intersects"
        feature = before.json()["features"][0]
        assert len(before.json()["features"]) == 1
        assert feature["id"] == "ci-central"
        assert feature["geometry"]["type"] == "MultiPolygon"
        assert feature["properties"]["value"] == 38.4
        assert feature["properties"]["quality_status"] == "synthetic_demo"
        assert datetime.fromisoformat(feature["properties"]["observed_at"]) == observed_at

        fixture("ci-synthetic-v2", 39.1, observed_at + timedelta(days=1))
        assert _refresh(client, path)["checkpoint"]["processed"] == 1
        second_event = _publication(lines)
        assert second_event["id"] != first_event["id"]
        assert second_event["layer_version"] == 2
        after = client.get("/api/map/features", params=query, headers={"If-None-Match": before.headers["ETag"]})
        assert after.status_code == 200
        assert after.json()["features"][0]["properties"]["value"] == 39.1
        assert after.headers["ETag"] != before.headers["ETag"]
        assert client.get("/api/map/features", params=query, headers={"If-None-Match": after.headers["ETag"]}).status_code == 304
    assert _refresh(client, path)["checkpoint"]["processed"] == 0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AreaIndicator)) == 2
        assert db.scalar(select(func.count()).select_from(PipelineEvent)) == 2
        assert db.scalar(select(func.count()).select_from(DatasetVersion).where(DatasetVersion.status == "published")) == 2
        assert db.execute(text("SELECT ST_SRID(geometry) FROM geographic_areas WHERE id='ci-central'")).scalar_one() == 4326
