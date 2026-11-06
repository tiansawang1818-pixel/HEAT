"""Autonomous Beat -> control queue -> ETL, using a local synthetic HTTP provider.

No application HTTP refresh endpoint is called. Never contacts public providers.
"""
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import redis
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

if os.environ.get("HEATSHIFT_RUN_STACK_TESTS") != "true":
    pytest.skip("Requires an isolated CI PostGIS/Redis stack", allow_module_level=True)

from app.db.session import engine
from app.models.entities import DataSource, DatasetVersion, SourceCheck
from app.services.bootstrap import ensure_sources


def test_beat_discovers_updates_skips_duplicates_and_preserves_validated_data(tmp_path):
    assert engine.url.database == "heatshift_ci" and engine.url.host in {"127.0.0.1", "localhost"}
    assert os.environ.get("ENVIRONMENT") == "test"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        assert connection.execute(text("SELECT 1 FROM pg_database WHERE datname='heatshift_scheduler_ci'")).scalar() is None
        connection.execute(text("CREATE DATABASE heatshift_scheduler_ci"))
    database_url = engine.url.set(database="heatshift_scheduler_ci")
    isolated = create_engine(database_url)
    api_root = Path(__file__).resolve().parents[1]
    state = {"revision": 0, "invalid": False}
    baseline = datetime.now(timezone.utc) - timedelta(days=2)

    class SyntheticProvider(BaseHTTPRequestHandler):
        def do_GET(self):
            moment = (baseline + timedelta(hours=state["revision"])).isoformat()
            body = json.dumps({
                "latitude": 13.7563, "longitude": 100.5018, "timezone": "UTC",
                "current": {"time": moment, "temperature_2m": "invalid" if state["invalid"] else 30 + state["revision"], "relative_humidity_2m": 60, "apparent_temperature": 32, "wind_speed_10m": 2},
                "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
                "hourly": {"time": [moment], "temperature_2m": [30], "apparent_temperature": [32], "relative_humidity_2m": [60], "precipitation_probability": [10]},
                "hourly_units": {"temperature_2m": "°C", "precipitation_probability": "%"},
            }).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

        def log_message(self, *args):
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticProvider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    env = {**os.environ, "DATABASE_URL": database_url.render_as_string(hide_password=False), "SCHEDULER_TICK_SECONDS": "2",
           "REDIS_URL": urlsplit(os.environ["REDIS_URL"])._replace(path="/3").geturl(),
           "OPEN_METEO_BASE_URL": "http://127.0.0.1:%s/forecast" % provider.server_port,
           "LOG_LEVEL": "WARNING", "DEMO_MODE": "false"}
    processes = []
    try:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=api_root, env=env, check=True, capture_output=True)
        with Session(isolated) as db:
            ensure_sources(db)
            for source in db.scalars(select(DataSource)):
                source.enabled = source.source_type == "weather"
                if source.enabled:
                    source.check_interval_seconds = 2  # Mock provider only; production cadence is unchanged.
            db.commit()
        commands = [
            [sys.executable, "-m", "celery", "-A", "app.worker:celery_app", "worker", "-Q", "celery,control", "--pool=solo", "--concurrency=1", "--loglevel=WARNING", "--without-gossip", "--without-mingle", "--without-heartbeat"],
            [sys.executable, "-m", "celery", "-A", "app.worker:celery_app", "beat", "--loglevel=WARNING", "--max-interval=1", "--schedule=" + str(tmp_path / "beat-state")],
        ]
        with ExitStack() as resources:
            for index, command in enumerate(commands):
                log = resources.enter_context((tmp_path / ("scheduler-%s.log" % index)).open("w"))
                processes.append(subprocess.Popen(command, cwd=api_root, env=env, stdout=log, stderr=subprocess.STDOUT))

            def wait_for(predicate):
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    assert all(process.poll() is None for process in processes), "Worker or Beat exited"
                    with Session(isolated) as db:
                        if predicate(db):
                            return
                    time.sleep(.2)
                pytest.fail("Autonomous scheduler publication did not reach its expected state")

            def published(db):
                return db.scalar(select(func.count()).select_from(DatasetVersion).where(DatasetVersion.status == "published"))

            wait_for(lambda db: published(db) == 1)
            with redis.Redis.from_url(env["REDIS_URL"], socket_timeout=2) as broker:
                assert float(broker.get("heatshift:dispatcher:last_tick")) > 0
            wait_for(lambda db: bool(db.scalar(select(SourceCheck.id).where(SourceCheck.discovered_count == 0, SourceCheck.status == "success"))))
            with Session(isolated) as db:
                assert published(db) == 1
            state["revision"] = 1
            wait_for(lambda db: published(db) == 2)
            state.update(revision=2, invalid=True)
            wait_for(lambda db: bool(db.scalar(select(DatasetVersion.id).where(DatasetVersion.status == "failed"))))
            with Session(isolated) as db:
                assert published(db) == 2
                latest = db.scalar(select(DatasetVersion).where(DatasetVersion.status == "published").order_by(DatasetVersion.observed_at.desc()))
                assert latest.metadata_json["provider_payload"]["current"]["temperature_2m"] == 31
    finally:
        for process in reversed(processes):
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        provider.shutdown(); provider.server_close(); isolated.dispose()
        if sys.exc_info()[0]:
            for path in tmp_path.glob("scheduler-*.log"):
                print(path.name, path.read_text()[-4000:])
