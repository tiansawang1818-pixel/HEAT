#!/usr/bin/env python3
"""Supervise the complete bounded runtime; persistent state stays in PG/Redis."""
import base64
import json
import os
from pathlib import Path
import signal
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PRIVATE_API_PORT = 18000


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
               RATE_LIMIT_ENABLED="true", XDG_CACHE_HOME=str(scratch / "cache"))
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
    return env


def runtime_commands(root):
    api = root / "apps" / "api"
    celery = [sys.executable, "-m", "celery", "-A", "app.worker.celery_app"]
    worker = ["worker", "--loglevel=INFO", "--pool=prefork", "--concurrency=1", "--without-gossip", "--without-mingle"]
    return {
        "api": ([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PRIVATE_API_PORT)], api),
        "web": (["node", "server.js"], root / "apps" / "web"),
        "ingestion": (celery + worker + ["-Q", "celery", "--hostname=ingestion@%h"], api),
        "dispatcher": (celery + worker + ["-Q", "control", "--hostname=dispatcher@%h"], api),
        "beat": (celery + ["beat", "--loglevel=INFO", "--schedule=/tmp/heatshift/celerybeat-schedule"], api),
    }


def stop_children(children, grace_seconds=25):
    for child in reversed(children):
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + grace_seconds
    for child in children:
        try:
            child.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)


def supervise(commands, env, stop=None, *, grace_seconds=25, on_started=None):
    stop = stop if stop is not None else threading.Event()
    children = {}
    try:
        for name, (command, cwd) in commands.items():
            children[name] = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
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

        return supervise(runtime_commands(root), env, stop, on_started=announce)
    except Exception as error:
        # Never print configuration, URI arguments, CA contents or exceptions.
        print(json.dumps({"event": "runtime.startup_failed", "phase": phase, "error_type": type(error).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
