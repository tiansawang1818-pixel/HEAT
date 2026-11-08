import base64
import importlib.util
import os
from pathlib import Path
import sys
import threading

import certifi
import pytest


spec = importlib.util.spec_from_file_location("hostless_runtime", Path(__file__).resolve().parents[3] / "scripts/hostless_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def managed_environment():
    return {
        "DATABASE_URL": "postgres://test:private-password@db.example.org/heatshift?sslmode=disable",
        "DATABASE_CA_PEM_BASE64": base64.b64encode(Path(certifi.where()).read_bytes()).decode(),
        "REDIS_URL": "rediss://test:private-password@cache.example.org:6380/0",
        "AUTH_REDIS_URL": "rediss://test:private-password@cache.example.org:6380/2",
        "AUTH_SESSION_SECRET": "isolated-test-secret-" * 3,
    }


def test_managed_connections_require_provider_ca_and_override_weaker_uri(tmp_path):
    from urllib.parse import parse_qs, urlsplit
    prepared = runtime.prepare_environment(managed_environment(), tmp_path)
    query = parse_qs(urlsplit(prepared["DATABASE_URL"]).query)
    assert query["sslmode"] == ["verify-full"]
    assert Path(query["sslrootcert"][0]).is_file()
    assert Path(query["sslrootcert"][0]).stat().st_mode & 0o777 == 0o600
    assert prepared["ADMIN_AUTH_MODE"] == "oidc" and prepared["ADMIN_API_KEY"] == ""
    assert prepared["DATABASE_MAX_OVERFLOW"] == "0"


@pytest.mark.parametrize("overrides", [
    {"DATABASE_CA_PEM_BASE64": ""}, {"DATABASE_CA_PEM_BASE64": "invalid"},
    {"DEMO_MODE": "true"}, {"ALLOW_FIXTURE_INGESTION": "true"},
    {"ADMIN_AUTH_MODE": "local_key"}, {"ENVIRONMENT": "development"},
    {"REDIS_URL": "redis://cache.example.org:6379/0"},
    {"AUTH_REDIS_URL": "rediss://cache.example.org/2?ssl_cert_reqs=none"},
    {"DATABASE_URL": "postgresql://db.example.org/heatshift?host=other.example.org"},
    {"AUTH_SESSION_SECRET": "short"},
])
def test_unsafe_or_incomplete_configuration_is_rejected(tmp_path, overrides):
    with pytest.raises((ValueError, OSError)):
        runtime.prepare_environment({**managed_environment(), **overrides}, tmp_path)


def test_only_explicit_isolated_ci_can_use_private_compose_backends(tmp_path):
    values = {**managed_environment(), "DATABASE_URL": "postgresql://postgres/heatshift_ci", "DATABASE_CA_PEM_BASE64": "", "REDIS_URL": "redis://redis/0", "AUTH_REDIS_URL": "redis://redis/2"}
    with pytest.raises(ValueError):
        runtime.prepare_environment(values, tmp_path)
    prepared = runtime.prepare_environment({**values, "CI": "true", "GITHUB_ACTIONS": "true", "HEATSHIFT_ISOLATED_CI": "true"}, tmp_path)
    assert prepared["DATABASE_URL"].startswith("postgresql+psycopg://postgres/heatshift_ci")


def test_child_exit_stops_all_other_processes(tmp_path, capsys):
    commands = {
        "waiting": ([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path),
        "failed": ([sys.executable, "-c", "raise SystemExit(2)"], tmp_path),
    }
    children = {}
    assert runtime.supervise(commands, dict(os.environ), grace_seconds=1, on_started=children.update) == 1
    assert all(child.poll() is not None for child in children.values())
    assert '"component": "failed"' in capsys.readouterr().out


def test_shutdown_event_reaps_children(tmp_path):
    stop = threading.Event()
    children = {}

    def started(value):
        children.update(value)
        stop.set()

    commands = {"waiting": ([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path)}
    assert runtime.supervise(commands, dict(os.environ), stop, grace_seconds=1, on_started=started) == 0
    assert all(child.poll() is not None for child in children.values())
