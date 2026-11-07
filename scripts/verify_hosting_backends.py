#!/usr/bin/env python3
"""Probe managed backends without starting local servers or exposing credentials.

Read-only by default. Explicit restart modes write only random, expiring Redis
probe keys, never Celery queues or application records. This does not provision,
resize, restart, install extensions, migrate data, or certify hosting costs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import ssl
import stat
from urllib.parse import unquote, urlsplit
import uuid


def error_reason(error):
    """Return only fixed labels; provider exceptions may contain credentials."""
    message = str(error).lower()
    for needles, label in (
        (("certificate verify failed", "certificate verification failed", "does not match", "hostname mismatch"), "tls_verification_failed"),
        (("eof detected", "unexpected eof", "connection closed by server"), "connection_closed_before_query"),
        (("password authentication failed", "wrongpass", "authentication required"), "authentication_failed"),
        (("timeout", "timed out"), "connection_timeout"),
        (("name or service not known", "could not translate host", "nodename nor servname", "name resolution"), "dns_failed"),
        (("connection refused",), "connection_refused"),
        (("noperm", "permission denied"), "permission_denied"),
        (("unknown command", "command is disabled", "command not allowed"), "command_unavailable"),
    ):
        if any(needle in message for needle in needles):
            return label
    return "backend_request_failed"


def load_credentials(path=None):
    if path:
        source = Path(path)
        metadata = source.stat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError("Credential file must be a private regular file (mode 0600)")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ValueError("Credential file must belong to the current user")
        values = json.loads(source.read_text())
    else:
        values = {
            "postgres_url": os.environ.get("HOSTING_POSTGRES_URL", ""),
            "redis_url": os.environ.get("HOSTING_REDIS_URL", ""),
        }
    if not isinstance(values, dict) or any(not isinstance(values.get(key, ""), str) for key in ("postgres_url", "redis_url")):
        raise ValueError("Expected postgres_url and redis_url strings")
    return values


def probe_postgres(uri):
    if not uri:
        return {"status": "unavailable", "reason": "missing_postgres_url"}
    try:
        scheme = urlsplit(uri).scheme
    except ValueError:
        scheme = ""
    if scheme not in ("postgres", "postgresql"):
        return {"status": "error", "reason": "postgres_uri_required"}
    import certifi
    import psycopg
    from psycopg.rows import dict_row

    try:
        with psycopg.connect(
            uri, sslmode="verify-full", sslrootcert=certifi.where(), connect_timeout=8,
            options="-c default_transaction_read_only=on -c statement_timeout=5000",
            row_factory=dict_row,
        ) as connection:
            report = {"status": "blocked", "tls_hostname_verified": True}
            report["server"] = connection.execute(
                "SELECT current_setting('server_version') AS version, pg_database_size(current_database()) AS database_bytes"
            ).fetchone()
            report["tls"] = connection.execute(
                "SELECT ssl, version FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
            ).fetchone()
            extensions = connection.execute(
                "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name LIKE 'postgis%' ORDER BY name"
            ).fetchall()
            report["spatial_extensions"] = extensions
            postgis = next((item for item in extensions if item["name"] == "postgis"), None)
            if not postgis:
                report["reason"] = "postgis_not_available"
            elif not postgis["installed_version"]:
                report["reason"] = "postgis_available_not_enabled"
            else:
                spatial = connection.execute(
                    "SELECT ST_SRID(ST_Transform(ST_SetSRID(ST_Point(100.5, 13.7), 4326), 3857)) AS projected_srid, "
                    "ST_IsValid(ST_GeomFromText('POLYGON((100 13,101 13,101 14,100 14,100 13))',4326)) AS valid_geometry"
                ).fetchone()
                report["spatial_check"] = spatial
                if spatial["projected_srid"] == 3857 and spatial["valid_geometry"] and report["tls"] and report["tls"]["ssl"]:
                    report["status"] = "passed"
                else:
                    report["reason"] = "spatial_or_tls_check_failed"
            return report
    except Exception as error:
        return {"status": "error", "reason": error_reason(error), "postgis_verified": False}


def redis_client(uri, database):
    import certifi
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    parsed = urlsplit(uri)
    if parsed.scheme != "rediss" or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("Use a rediss URI without query overrides; TLS verification is mandatory")
    return redis.Redis(
        host=parsed.hostname, port=parsed.port or 6379, db=database,
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        ssl=True, ssl_cert_reqs=ssl.CERT_REQUIRED, ssl_check_hostname=True,
        ssl_ca_certs=certifi.where(), socket_connect_timeout=8, socket_timeout=8,
        retry=Retry(NoBackoff(), 0), decode_responses=True,
    )


def check_restart(client, marker):
    """A reconnect alone is insufficient: require a different server process."""
    run_id = client.info("server")["run_id"]
    if run_id == marker["run_id"]:
        return {"status": "pending", "reason": "server_restart_not_observed"}
    actual = client.lrange(marker["queue_key"], 0, -1)
    checksum = hashlib.sha256(json.dumps(actual, separators=(",", ":")).encode()).hexdigest()
    retained = client.get(marker["marker_key"]) == marker["nonce"] and checksum == marker["queue_sha256"]
    return {"status": "passed" if retained else "failed", "server_restart_observed": True, "queue_retained": retained}


def validate_marker(marker):
    namespace = "heatshift:hosting-probe:"
    probe_id = marker.get("probe_id", "")
    if str(uuid.UUID(probe_id)) != probe_id or not isinstance(marker.get("run_id"), str):
        raise ValueError("Invalid restart marker")
    if marker.get("marker_key") != namespace + probe_id + ":marker" or marker.get("queue_key") != namespace + probe_id + ":queue":
        raise ValueError("Restart keys must belong to the isolated probe namespace")
    if not all(isinstance(marker.get(key), str) for key in ("nonce", "queue_sha256")):
        raise ValueError("Invalid restart checksum")


def prepare_restart(client, path):
    probe_id = str(uuid.uuid4())
    prefix = "heatshift:hosting-probe:" + probe_id
    messages = ["probe-first:" + probe_id, "probe-second:" + probe_id]
    marker = {
        "probe_id": probe_id, "run_id": client.info("server")["run_id"],
        "marker_key": prefix + ":marker", "queue_key": prefix + ":queue", "nonce": uuid.uuid4().hex,
        "queue_sha256": hashlib.sha256(json.dumps(messages, separators=(",", ":")).encode()).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(), "ttl_seconds": 3600,
    }
    # Create the local marker before remote writes; never replace another probe.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(marker, output)
    with client.pipeline(transaction=True) as pipeline:
        pipeline.set(marker["marker_key"], marker["nonce"], ex=3600, nx=True)
        pipeline.rpush(marker["queue_key"], *messages)
        pipeline.expire(marker["queue_key"], 3600)
        results = pipeline.execute()
    if not results[0] or results[1] != 2:
        raise RuntimeError("Probe keys could not be prepared")
    return {"status": "pending", "reason": "restart_required", "ttl_seconds": 3600}


def probe_redis(uri, prepare_path=None, verify_path=None):
    if not uri:
        return {"status": "unavailable", "reason": "missing_redis_url"}
    report = {"status": "blocked", "restart": {"status": "not_verified"}}
    clients = []
    try:
        clients = [redis_client(uri, database) for database in (0, 1, 2)]
        if not all(client.ping() for client in clients):
            return {"status": "error", "reason": "redis_ping_failed"}
        report["tls_hostname_verified"] = True
        report["logical_databases"] = [0, 1, 2]
        client = clients[0]
        persistence = client.info("persistence")
        report["persistence"] = {key: persistence.get(key) for key in ("aof_enabled", "aof_last_write_status", "rdb_last_bgsave_status")}
        memory = client.info("memory")
        report["memory"] = {key: memory.get(key) for key in ("used_memory", "maxmemory", "maxmemory_policy")}
        try:
            configuration = client.config_get("appendonly", "appendfsync", "maxmemory-policy", "databases")
            report["configuration"] = configuration
        except Exception as error:
            report["configuration"] = {"status": "not_verified", "reason": error_reason(error)}
            configuration = {}
        if prepare_path:
            report["restart"] = prepare_restart(client, prepare_path)
        if verify_path:
            marker = json.loads(Path(verify_path).read_text())
            validate_marker(marker)
            report["restart"] = check_restart(client, marker)
        report["durable_configuration_verified"] = (
            persistence.get("aof_enabled") == 1
            and persistence.get("aof_last_write_status") == "ok"
            and configuration.get("appendonly") == "yes"
            and configuration.get("appendfsync") in ("everysec", "always")
            and configuration.get("maxmemory-policy") == "noeviction"
        )
        if report["durable_configuration_verified"] and report["restart"]["status"] == "passed":
            report["status"] = "passed"
        else:
            report["reason"] = "persistence_or_restart_not_verified"
        return report
    except ValueError:
        return {"status": "error", "reason": "invalid_redis_uri_or_restart_marker"}
    except Exception as error:
        report.update(status="error", reason=error_reason(error))
        return report
    finally:
        for client in clients:
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-file", help="Private JSON file, mode 0600; never pass passwords as arguments")
    parser.add_argument("--output", type=Path)
    restart = parser.add_mutually_exclusive_group()
    restart.add_argument("--prepare-restart", type=Path, help="Create expiring isolated Redis probe keys and a new marker file")
    restart.add_argument("--verify-restart", type=Path, help="Verify queue retention after an operator restarts the test Redis")
    args = parser.parse_args()
    try:
        credentials = load_credentials(args.credentials_file)
    except (ValueError, OSError, json.JSONDecodeError):
        print(json.dumps({"status": "error", "reason": "missing_or_invalid_private_credentials_file"}))
        return 2
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "managed_backend_compatibility_only",
        "postgres": probe_postgres(credentials.get("postgres_url", "")),
        "redis": probe_redis(credentials.get("redis_url", ""), args.prepare_restart, args.verify_restart),
        "whole_application_capacity_verified": False, "zero_cost_verified": False,
    }
    report["status"] = "passed" if all(report[name]["status"] == "passed" for name in ("postgres", "redis")) else "blocked"
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
