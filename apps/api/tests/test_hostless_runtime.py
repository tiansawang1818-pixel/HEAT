import base64
import importlib.util
import json
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
    assert prepared["API_BOOTSTRAP_MARKER"] == str(tmp_path / "api-bootstrapped")


def test_hostless_assigned_port_8000_keeps_web_and_api_separate(tmp_path):
    from urllib.parse import urlsplit
    prepared = runtime.prepare_environment({**managed_environment(), "PORT": "8000"}, tmp_path)
    commands = runtime.runtime_commands(tmp_path)
    api = commands["api"][0]
    api_port = api[api.index("-b") + 1].rsplit(":", 1)[1]
    assert prepared["PORT"] == "8000"
    assert api_port != prepared["PORT"]
    assert urlsplit(prepared["API_INTERNAL_URL"]).port == int(api_port)


def test_earth_engine_secret_is_materialized_without_inheriting_encoded_key(tmp_path):
    key = {
        "type": "service_account", "project_id": "heatshift-test",
        "client_email": "reader@heatshift-test.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
        "private_key": "-----BEGIN PRIVATE KEY-----\nTEST_PLACEHOLDER",
    }
    encoded = base64.b64encode(json.dumps(key).encode()).decode()
    env = {**managed_environment(), "GEE_PROJECT": "heatshift-test", "GEE_SERVICE_ACCOUNT_JSON_BASE64": encoded}
    prepared = runtime.prepare_environment(env, tmp_path)
    credential_file = Path(prepared["GOOGLE_APPLICATION_CREDENTIALS"])
    assert "GEE_SERVICE_ACCOUNT_JSON_BASE64" not in prepared
    assert json.loads(credential_file.read_text()) == key
    assert credential_file.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="Invalid Earth Engine service account configuration"):
        runtime.prepare_environment({**env, "GEE_PROJECT": "other-project"}, tmp_path)
    with pytest.raises(ValueError, match="one credential source"):
        runtime.prepare_environment({**env, "GOOGLE_APPLICATION_CREDENTIALS": "/secrets/other.json"}, tmp_path)


@pytest.mark.parametrize("overrides", [
    {"DATABASE_CA_PEM_BASE64": ""}, {"DATABASE_CA_PEM_BASE64": "invalid"},
    {"DEMO_MODE": "true"}, {"ALLOW_FIXTURE_INGESTION": "true"},
    {"ADMIN_AUTH_MODE": "local_key"}, {"ENVIRONMENT": "development"},
    {"REDIS_URL": "redis://cache.example.org:6379/0"},
    {"AUTH_REDIS_URL": "rediss://cache.example.org/2?ssl_cert_reqs=none"},
    {"DATABASE_URL": "postgresql://db.example.org/heatshift?host=other.example.org"},
    {"AUTH_SESSION_SECRET": "short"},
    {"PORT": str(runtime.PRIVATE_API_PORT)},
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


def test_dispatcher_runs_in_process_and_ingestion_recycles_large_children(tmp_path):
    commands = runtime.runtime_commands(tmp_path)
    assert "--pool=solo" in commands["dispatcher"][0]
    ingestion = commands["ingestion"][0]
    assert "--pool=prefork" in ingestion and "--max-memory-per-child=150000" in ingestion


def test_api_gets_a_larger_pool_within_the_managed_connection_budget():
    assert runtime.PROCESS_ENV["api"]["DATABASE_POOL_SIZE"] == "4"
    assert runtime.PROCESS_ENV["api"]["API_RECYCLE_RSS_MB"] == "250"
    # API 4 + ingestion child 2 + dispatcher 2 stays under the 10-connection role limit.
    assert 4 + 2 + 2 < 10


def test_python_processes_limit_malloc_arenas_and_web_caps_its_heap():
    for name in ("api", "ingestion", "dispatcher"):
        assert runtime.PROCESS_ENV[name]["MALLOC_ARENA_MAX"] == "2"
    assert runtime.PROCESS_ENV["web"]["NODE_OPTIONS"] == "--max-old-space-size=256"


def test_web_starts_only_after_the_api_listens(tmp_path, monkeypatch):
    started_before_wait = []

    def waited(port, child, stop, timeout_seconds=120):
        started_before_wait.append((port, sorted(spawned)))
        return True

    spawned = []
    real_popen = runtime.subprocess.Popen

    def popen(command, **kwargs):
        spawned.append(command[-1])
        return real_popen(command, **kwargs)

    monkeypatch.setattr(runtime, "wait_for_port", waited)
    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    stop = threading.Event()
    commands = {
        "api": ([sys.executable, "-c", "import time; time.sleep(30) # api"], tmp_path),
        "web": ([sys.executable, "-c", "import time; time.sleep(30) # web"], tmp_path),
    }
    runtime.supervise(commands, dict(os.environ), stop, grace_seconds=1,
                      on_started=lambda _: stop.set(), ready_ports={"api": 18000})
    assert started_before_wait == [(18000, ["import time; time.sleep(30) # api"])]
    assert len(spawned) == 2


def test_beat_runs_embedded_in_the_dispatcher(tmp_path):
    commands = runtime.runtime_commands(tmp_path)
    assert "beat" not in commands
    dispatcher = commands["dispatcher"][0]
    assert "-B" in dispatcher and "--pool=solo" in dispatcher and "-Q" in dispatcher


def test_api_worker_is_recycled_to_cap_memory_growth(tmp_path):
    api = runtime.runtime_commands(tmp_path)["api"][0]
    assert api[1:3] == ["-m", "gunicorn"] and "app.core.gunicorn_worker.ApiWorker" in api
    assert api[api.index("-w") + 1] == "1"
    assert int(api[api.index("--max-requests") + 1]) > 0
    assert api[api.index("-b") + 1] == "127.0.0.1:%d" % runtime.PRIVATE_API_PORT
    # Preloading would keep a second copy of the app in the master process.
    assert "--preload" not in api


def test_shutdown_stops_web_before_the_api_it_proxies(tmp_path):
    stopped = []
    marker = tmp_path / "order.txt"
    script = ("import signal,sys,time,pathlib\n"
              "def bye(*_):\n"
              "    with open(%r,'a') as f: f.write(sys.argv[1]+'\\n')\n"
              "    sys.exit(0)\n"
              "signal.signal(signal.SIGTERM, bye)\n"
              "time.sleep(30)\n") % str(marker)
    import subprocess as sp
    children = [sp.Popen([sys.executable, "-c", script, name], start_new_session=True) for name in ("api", "web")]
    import time as t
    t.sleep(1)
    runtime.stop_children(children, grace_seconds=10)
    assert marker.read_text().split() == ["web", "api"]


def test_migrations_wait_for_the_previous_container_to_release_connections(tmp_path):
    import subprocess as sp
    calls = []

    def flaky(args, **kwargs):
        calls.append(args)
        if len(calls) < 3:
            raise sp.CalledProcessError(1, args)
    runtime.run_migrations(tmp_path, {}, threading.Event(), attempts=5, delay_seconds=0, run=flaky)
    assert len(calls) == 3

    def always(args, **kwargs):
        raise sp.CalledProcessError(1, args)
    with pytest.raises(sp.CalledProcessError):
        runtime.run_migrations(tmp_path, {}, threading.Event(), attempts=2, delay_seconds=0, run=always)


def test_api_worker_bounds_shutdown_so_event_streams_cannot_stall_a_recycle():
    from app.core.gunicorn_worker import ApiWorker
    assert ApiWorker.CONFIG_KWARGS["timeout_graceful_shutdown"] <= 5
    assert ApiWorker.CONFIG_KWARGS["loop"] == "auto"
