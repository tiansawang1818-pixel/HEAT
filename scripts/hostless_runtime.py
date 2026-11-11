#!/usr/bin/env python3
"""Supervise the complete bounded runtime; persistent state stays in PG/Redis."""
import base64
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PRIVATE_API_PORT = 18000


def prepare_earth_engine_credentials(env, scratch):
    encoded = env.pop("GEE_SERVICE_ACCOUNT_JSON_BASE64", "")
    if not encoded:
        return
    project = env.get("GEE_PROJECT", "")
    if not project or len(encoded) > 32_768 or env.get("GOOGLE_APPLICATION_CREDENTIALS"):
        raise ValueError("Earth Engine requires a project and one credential source")
    try:
        raw = base64.b64decode(encoded, validate=True)
        key = json.loads(raw)
        if (not isinstance(key, dict) or key.get("type") != "service_account"
                or key.get("project_id") != project or key.get("token_uri") != "https://oauth2.googleapis.com/token"
                or not str(key.get("client_email", "")).endswith(f"@{project}.iam.gserviceaccount.com")
                or not str(key.get("private_key", "")).startswith("-----BEGIN PRIVATE KEY-----\n")):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Invalid Earth Engine service account configuration") from None
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_path = scratch / "earthengine-key.json"
    with tempfile.NamedTemporaryFile(dir=scratch, prefix="earthengine-", delete=False) as output:
        temporary_path = Path(output.name)
        try:
            output.write(raw)
            output.flush()
            temporary_path.replace(key_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    env["GOOGLE_APPLICATION_CREDENTIALS"] = str(key_path)


def prepare_environment(source, scratch):
    env = dict(source)
    for key in ("DEMO_MODE", "ALLOW_FIXTURE_INGESTION", "NEXT_PUBLIC_DEMO_MODE"):
        if env.get(key, "false").lower() not in {"false", "0"}:
            raise ValueError("Synthetic data is not allowed in this runtime")
        env[key] = "false"
    if env.get("ENVIRONMENT", "production") != "production" or env.get("ADMIN_AUTH_MODE", "oidc") != "oidc":
        raise ValueError("Production OIDC administration is mandatory")
    if len(env.get("AUTH_SESSION_SECRET", "")) < 32:
        raise ValueError("A private session encryption secret is required")
    env.update(ENVIRONMENT="production", ADMIN_AUTH_MODE="oidc", ADMIN_API_KEY="",
               DASHBOARD_ADMIN_TOKEN="", DATABASE_POOL_SIZE="2", DATABASE_MAX_OVERFLOW="0",
               DATABASE_POOL_TIMEOUT_SECONDS="10", HOSTNAME="0.0.0.0",
               API_INTERNAL_URL=f"http://127.0.0.1:{PRIVATE_API_PORT}", NEXT_PUBLIC_API_URL="",
               RATE_LIMIT_ENABLED="true", METRICS_REQUIRE_ADMIN="true", XDG_CACHE_HOME=str(scratch / "cache"))
    env["HOSTLESS_COMBINED_RUNTIME"] = "true"
    parsed = urlsplit(env.get("DATABASE_URL", ""))
    if parsed.scheme not in {"postgres", "postgresql", "postgresql+psycopg"} or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("Explicit PostgreSQL database is required")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if {"host", "hostaddr", "port", "dbname", "service", "servicefile"}.intersection(query):
        raise ValueError("Database routing overrides are not supported")
    isolated_ci = all(env.get(key) == "true" for key in ("CI", "GITHUB_ACTIONS", "HEATSHIFT_ISOLATED_CI"))
    if isolated_ci and parsed.hostname == "postgres":
        # Only the disposable CI Compose network uses private plain transport.
        query = {}
    else:
        encoded_ca = env.get("DATABASE_CA_PEM_BASE64", "")
        if not encoded_ca or len(encoded_ca) > 2_800_000:
            raise ValueError("An explicit provider CA bundle is required")
        pem = base64.b64decode(encoded_ca, validate=True)
        context = ssl.create_default_context(cadata=pem.decode("ascii"))
        if not context.get_ca_certs():
            raise ValueError("Provider CA is invalid")
        scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
        ca_path = scratch / "postgres-ca.pem"
        descriptor = os.open(ca_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(pem)
        query.update(sslmode="verify-full", sslrootcert=str(ca_path), connect_timeout="8")
    env["DATABASE_URL"] = urlunsplit(("postgresql+psycopg", parsed.netloc, parsed.path, urlencode(query), ""))
    for key in ("REDIS_URL", "AUTH_REDIS_URL"):
        redis = urlsplit(env.get(key, ""))
        private_ci = isolated_ci and redis.scheme == "redis" and redis.hostname == "redis"
        if not redis.hostname or redis.query or redis.fragment or (redis.scheme != "rediss" and not private_ci):
            raise ValueError("Managed Redis requires TLS without URL overrides")
    port = int(env.get("PORT", "3000"))
    if not 1024 <= port <= 65535:
        raise ValueError("Invalid web port")
    if port == PRIVATE_API_PORT:
        raise ValueError("Assigned web port conflicts with the private API port")
    env["PORT"] = str(port)
    prepare_earth_engine_credentials(env, scratch)
    return env


# The managed role allows 10 connections. Each process has its own pool:
# API 4 + ingestion child 2 + dispatcher 2 = 8, leaving room for migrations.
# The API serves every browser at ~0.5-1 s per remote query, so two
# connections queued past the 10 s pool timeout during restarts (HTTP 500).
# glibc gives each thread its own malloc arena; two arenas bound the
# fragmentation of the threaded API and Celery workers. Node's heap is capped
# so the web server collects garbage before it grows into the shared budget.
PROCESS_ENV = {
    "api": {"DATABASE_POOL_SIZE": "4", "MALLOC_ARENA_MAX": "2"},
    "ingestion": {"MALLOC_ARENA_MAX": "2"},
    "dispatcher": {"MALLOC_ARENA_MAX": "2"},
    "web": {"NODE_OPTIONS": "--max-old-space-size=256"},
}


def runtime_commands(root):
    api = root / "apps" / "api"
    celery = [sys.executable, "-m", "celery", "-A", "app.worker.celery_app"]
    worker = ["worker", "--loglevel=INFO", "--concurrency=1", "--without-gossip", "--without-mingle"]
    # 768 MiB holds six Python processes plus Node. Ingestion keeps a prefork
    # child so hard time limits still apply, and replaces it after any task
    # that leaves it above 150 MB. The dispatcher only runs a short bounded
    # dispatch task, so it runs in-process instead of forking a second copy.
    ingestion = worker + ["--pool=prefork", "--max-memory-per-child=150000"]
    # Beat runs embedded in the dispatcher (-B): it only publishes two task
    # names on a timetable, and a separate process cost about 90 MB.
    dispatcher = worker + ["--pool=solo", "-B", "--schedule=/tmp/heatshift/celerybeat-schedule"]
    return {
        # Python never returns freed heap to the OS, so the API grew past 300 MB
        # and pushed the container into its memory limit. Gunicorn keeps the
        # listening socket and replaces its single worker after about 400 requests
        # of requests; requests queue briefly instead of being refused.
        "api": ([sys.executable, "-m", "gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker",
                 "-w", "1", "-b", "127.0.0.1:%d" % PRIVATE_API_PORT, "--max-requests", "400",
                 "--max-requests-jitter", "100", "--timeout", "180", "--graceful-timeout", "30",
                 "--keep-alive", "5"], api),
        "web": (["node", "server.js"], root / "apps" / "web"),
        "ingestion": (celery + ingestion + ["-Q", "celery", "--hostname=ingestion@%h"], api),
        "dispatcher": (celery + dispatcher + ["-Q", "control", "--hostname=dispatcher@%h"], api),
    }


def stop_children(children, grace_seconds=25, step_seconds=8):
    """Stop children in reverse start order, letting each exit before the next.

    Start order is api, web, ingestion, dispatcher, so the public web server
    stops before the private API it proxies to; signalling both at once let
    the API close while the web was still forwarding a request ("socket hang
    up"). Anything still running at the overall deadline is killed.
    """
    deadline = time.monotonic() + grace_seconds
    for child in reversed(children):
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            try:
                child.wait(timeout=max(0.01, min(step_seconds, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
    for child in children:
        try:
            child.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)


def wait_for_port(port, child, stop, timeout_seconds=120):
    """Return once something listens on localhost:port, or the child or deadline ends."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and child.poll() is None and not stop.is_set():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            stop.wait(0.5)
    return False


def supervise(commands, env, stop=None, *, grace_seconds=25, on_started=None, ready_ports=None):
    stop = stop if stop is not None else threading.Event()
    children = {}
    try:
        for name, (command, cwd) in commands.items():
            children[name] = subprocess.Popen(command, cwd=cwd, env={**env, **PROCESS_ENV.get(name, {})},
                                              start_new_session=True)
            # Start the public web server only after the private API listens, so
            # health checks during a deploy are not proxied to a closed port.
            if ready_ports and name in ready_ports:
                wait_for_port(ready_ports[name], children[name], stop)
        if on_started:
            on_started(children)
        while not stop.wait(0.2):
            for name, child in children.items():
                if child.poll() is not None:
                    print(json.dumps({"event": "runtime.child_exited", "component": name, "exit_code": child.returncode}), flush=True)
                    return 1
        return 0
    finally:
        stop_children(list(children.values()), grace_seconds)


def main():
    root = Path(__file__).resolve().parents[1]
    scratch = Path("/tmp/heatshift")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    phase = "configuration"
    try:
        print(json.dumps({"event": "runtime.boot", "phase": phase}), flush=True)
        env = prepare_environment(os.environ, scratch)
        scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
        phase = "migrations"
        migration_started = time.monotonic()
        print(json.dumps({"event": "runtime.boot", "phase": phase}), flush=True)
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root / "apps" / "api", env=env, check=True, timeout=120)
        print(json.dumps({"event": "runtime.migrations_completed", "duration_ms": round((time.monotonic() - migration_started) * 1000)}), flush=True)
        phase = "processes"

        def announce(children):
            state = {name: child.pid for name, child in children.items()}
            (scratch / "processes.json").write_text(json.dumps(state))
            print(json.dumps({"event": "runtime.started", "components": list(state)}), flush=True)

        return supervise(runtime_commands(root), env, stop, on_started=announce, ready_ports={"api": PRIVATE_API_PORT})
    except Exception as error:
        # Never print configuration, URI arguments, CA contents or exceptions.
        print(json.dumps({"event": "runtime.startup_failed", "phase": phase, "error_type": type(error).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
