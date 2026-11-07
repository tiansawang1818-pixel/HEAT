"""A backend probe must not convert missing evidence into deployment readiness."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "hosting_probe", Path(__file__).resolve().parents[3] / "scripts" / "verify_hosting_backends.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize("message,reason", [
    ("password authentication failed secret-value", "authentication_failed"),
    ("SSL SYSCALL error: EOF detected", "connection_closed_before_query"),
    ("certificate verify failed: secret-uri", "tls_verification_failed"),
    ("connection timed out", "connection_timeout"),
    ("canceling statement due to statement timeout", "query_timeout"),
    ("canceling statement due to lock timeout", "lock_timeout"),
    ("NOPERM command unavailable", "permission_denied"),
    ("User gateway has no permissions to run config|get", "permission_denied"),
    ("unknown command CONFIG private-details", "command_unavailable"),
    ("unexpected provider details secret-value", "backend_request_failed"),
])
def test_errors_are_fixed_labels_without_credentials(message, reason):
    assert probe.error_reason(RuntimeError(message)) == reason
    assert "secret" not in probe.error_reason(RuntimeError(message))


def test_missing_credentials_do_not_probe_application_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://private-application-database")
    monkeypatch.delenv("HOSTING_POSTGRES_URL", raising=False)
    monkeypatch.delenv("HOSTING_REDIS_URL", raising=False)
    assert probe.load_credentials() == {"postgres_url": "", "redis_url": ""}
    assert probe.probe_postgres("")["status"] == "unavailable"
    assert probe.probe_redis("")["status"] == "unavailable"


def test_credentials_file_must_not_be_readable_by_other_users(tmp_path):
    source = tmp_path / "credentials.json"
    source.write_text('{"postgres_url":"postgresql://secret"}')
    source.chmod(0o644)
    with pytest.raises(ValueError, match="private regular file"):
        probe.load_credentials(source)
    source.chmod(0o600)
    assert probe.load_credentials(source)["postgres_url"] == "postgresql://secret"


@pytest.mark.parametrize("uri", ["redis://example.org/0", "rediss://example.org/0?ssl_cert_reqs=none", "rediss://example.org/0#fragment"])
def test_redis_tls_cannot_be_disabled_with_uri_overrides(uri):
    with pytest.raises(ValueError):
        probe.redis_client(uri, 0)


def test_redis_uses_hostname_sni_and_required_certificates(monkeypatch):
    import redis
    captured = {}
    monkeypatch.setattr(redis, "Redis", lambda **options: captured.update(options))
    probe.redis_client("rediss://default:pass%2Fword@db.example.org:6380/0", 2)
    assert captured["host"] == "db.example.org"
    assert captured["db"] == 2
    assert captured["password"] == "pass/word"
    assert captured["ssl"] and captured["ssl_check_hostname"]
    assert captured["ssl_cert_reqs"] == probe.ssl.CERT_REQUIRED


class FakePostgres:
    def __init__(self, extensions):
        self.extensions = extensions
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query):
        self.queries.append(query)
        if "pg_available_extensions" in query:
            return SimpleNamespace(fetchall=lambda: self.extensions)
        if "pg_stat_ssl" in query:
            row = {"ssl": True, "version": "TLSv1.3"}
        elif "ST_SRID" in query:
            row = {"projected_srid": 3857, "valid_geometry": True}
        else:
            row = {"version": "17.1", "database_bytes": 10_000_000}
        return SimpleNamespace(fetchone=lambda: row)


@pytest.mark.parametrize("installed,reason", [(None, "postgis_available_not_enabled"), (False, "postgis_not_available")])
def test_postgres_without_working_postgis_is_blocked(monkeypatch, installed, reason):
    import psycopg
    connection = FakePostgres([] if installed is False else [{"name": "postgis", "default_version": "3.4", "installed_version": installed}])
    captured = {}

    def connect(uri, **options):
        captured.update(options)
        return connection

    monkeypatch.setattr(psycopg, "connect", connect)
    report = probe.probe_postgres("postgresql://user:secret@db.example.org/db?sslmode=disable")
    assert report["status"] == "blocked" and report["reason"] == reason
    assert captured["sslmode"] == "verify-full"
    assert "default_transaction_read_only=on" in captured["options"]
    assert not any("CREATE" in query or "INSERT" in query for query in connection.queries)


def test_postgres_pass_requires_spatial_query(monkeypatch):
    import psycopg
    connection = FakePostgres([{"name": "postgis", "default_version": "3.4", "installed_version": "3.4"}])
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: connection)
    assert probe.probe_postgres("postgresql://db.example.org/db")["status"] == "passed"
    assert any("ST_Transform" in query for query in connection.queries)


def test_explicit_provider_ca_overrides_uri_without_weakening_tls(monkeypatch, tmp_path):
    import certifi
    import psycopg
    ca_file = tmp_path / "project-ca.pem"
    ca_file.write_bytes(Path(certifi.where()).read_bytes())
    captured = {}
    connection = FakePostgres([{"name": "postgis", "default_version": "3.4", "installed_version": "3.4"}])

    def connect(uri, **options):
        captured.update(options)
        return connection

    monkeypatch.setattr(psycopg, "connect", connect)
    report = probe.probe_postgres("postgresql://db.example.org/db?sslmode=disable&sslrootcert=/untrusted.pem", ca_file=ca_file)
    assert report["status"] == "passed"
    assert captured["sslmode"] == "verify-full"
    assert captured["sslrootcert"] == str(ca_file.resolve())
    assert "default_transaction_read_only=on" in captured["options"]


@pytest.mark.parametrize("content", [b"", b"private-invalid-pem", b"x" * 2_000_001])
def test_invalid_provider_ca_never_contacts_database(monkeypatch, tmp_path, content):
    import psycopg
    ca_file = tmp_path / "project-ca.pem"
    ca_file.write_bytes(content)
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: pytest.fail("Invalid CA must fail before connecting"))
    assert probe.probe_postgres("postgresql://db.example.org/db", ca_file=ca_file) == {
        "status": "error", "reason": "invalid_postgres_ca_file",
    }


def test_cli_invalid_provider_ca_does_not_read_credentials_or_probe_redis(monkeypatch, tmp_path, capsys):
    private_path = tmp_path / "private-ca-name.pem"
    monkeypatch.setattr(probe.sys, "argv", ["verify_hosting_backends.py", "--postgres-ca-file", str(private_path)])
    monkeypatch.setattr(probe, "load_credentials", lambda *args: pytest.fail("CA validation precedes credential access"))
    monkeypatch.setattr(probe, "probe_redis", lambda *args: pytest.fail("Invalid CA must not trigger unrelated writes"))
    assert probe.main() == 2
    output = capsys.readouterr().out
    assert "private-ca-name" not in output
    assert json.loads(output) == {"status": "error", "reason": "invalid_postgres_ca_file"}


def test_cli_passes_provider_ca_to_both_postgres_probes(monkeypatch, tmp_path, capsys):
    import certifi
    ca_file = tmp_path / "project-ca.pem"
    ca_file.write_bytes(Path(certifi.where()).read_bytes())
    monkeypatch.setattr(probe.sys, "argv", ["verify_hosting_backends.py", "--postgres-ca-file", str(ca_file), "--postgis-transaction-test"])
    monkeypatch.setattr(probe, "load_credentials", lambda *args: {"postgres_url": "approved-test-uri"})
    seen = []

    def inspect(*args, **options):
        seen.append(options["ca_file"])
        return {"status": "passed"}

    monkeypatch.setattr(probe, "probe_postgres", inspect)
    monkeypatch.setattr(probe, "probe_transactional_postgis", inspect)
    monkeypatch.setattr(probe, "probe_redis", lambda *args: {"status": "passed"})
    assert probe.main() == 0
    assert seen == [str(ca_file.resolve()), str(ca_file.resolve())]
    assert str(ca_file) not in capsys.readouterr().out


class FakeRedis:
    def __init__(self, run_id="original", config_allowed=True):
        self.run_id = run_id
        self.config_allowed = config_allowed
        self.marker = None
        self.messages = []

    def ping(self):
        return True

    def close(self):
        pass

    def info(self, section):
        if section == "server":
            return {"run_id": self.run_id}
        return {"aof_enabled": 1, "aof_last_write_status": "ok", "rdb_last_bgsave_status": "ok"}

    def config_get(self, *args):
        if not self.config_allowed:
            raise RuntimeError("NOPERM secret-value")
        return {"appendonly": "yes", "appendfsync": "everysec", "maxmemory-policy": "noeviction", "databases": "16"}

    def get(self, key):
        return self.marker

    def lrange(self, key, start, end):
        return self.messages


@pytest.mark.parametrize("config_allowed", [True, False])
def test_aof_or_ping_does_not_prove_restart_retention(monkeypatch, config_allowed):
    client = FakeRedis(config_allowed=config_allowed)
    monkeypatch.setattr(probe, "redis_client", lambda *args: client)
    report = probe.probe_redis("rediss://db.example.org")
    assert report["status"] == "blocked"
    assert report["restart"]["status"] == "not_verified"
    assert "secret-value" not in json.dumps(report)


def test_reconnect_to_same_process_does_not_count_as_restart():
    result = probe.check_restart(FakeRedis(), {"run_id": "original"})
    assert result == {"status": "pending", "reason": "server_restart_not_observed"}


def test_restart_must_preserve_queue_order_and_marker():
    client = FakeRedis(run_id="new-process")
    client.marker = "nonce"
    client.messages = ["first", "second"]
    marker = {"run_id": "old-process", "marker_key": "marker", "queue_key": "queue", "nonce": "nonce",
              "queue_sha256": hashlib.sha256(b'["first","second"]').hexdigest()}
    assert probe.check_restart(client, marker)["status"] == "passed"
    client.messages.reverse()
    assert probe.check_restart(client, marker)["status"] == "failed"
    client.messages = ["first", "second"]
    client.marker = None
    assert probe.check_restart(client, marker)["status"] == "failed"


def test_restart_marker_cannot_target_application_queues():
    with pytest.raises(ValueError):
        probe.validate_marker({"probe_id": "00000000-0000-0000-0000-000000000000", "run_id": "old", "queue_key": "celery"})


@pytest.mark.parametrize("uri", ["postgresql://db.example.org/heatshift", "postgresql://db.example.org/heat-pg16-check-other", "postgresql://db.example.org/defaultdb", "postgresql://db.example.org/heat-postgis-check?dbname=defaultdb", "mysql://db.example.org/heat-pg16-check", "postgresql:///heat-pg16-check", "postgresql://db.example.org/heat-pg16-check?dbname=heatshift", "postgresql://db.example.org/heat-pg16-check?service=production"])
def test_transactional_spatial_probe_refuses_other_database_names_before_connect(monkeypatch, uri):
    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: pytest.fail("Wrong database must not be contacted"))
    result = probe.probe_transactional_postgis(uri)
    assert result["status"] == "blocked" and result["writes_attempted"] is False


def test_transactional_spatial_probe_cannot_proceed_with_application_tables():
    from app.services.hosting_postgis import rollback_postgis_probe
    queries = []

    class OccupiedDatabase:
        autocommit = True
        info = SimpleNamespace(transaction_status=0)

        def execute(self, query):
            queries.append(query)
            return SimpleNamespace(fetchall=lambda: [{"tablename": "geographic_areas"}])

    result = rollback_postgis_probe(OccupiedDatabase())
    assert result["status"] == "blocked" and result["writes_attempted"] is False
    assert all(query.startswith("SELECT") for query in queries)


def test_requested_transactional_probe_failure_prevents_success(monkeypatch, capsys):
    monkeypatch.setattr(probe.sys, "argv", ["verify_hosting_backends.py", "--postgis-transaction-test"])
    monkeypatch.setattr(probe, "load_credentials", lambda *args: {"postgres_url": "approved-test-uri", "redis_url": "test-uri"})
    monkeypatch.setattr(probe, "probe_postgres", lambda *args: {"status": "passed"})
    monkeypatch.setattr(probe, "probe_redis", lambda *args: {"status": "passed"})
    monkeypatch.setattr(probe, "probe_transactional_postgis", lambda *args, **kwargs: {"status": "blocked", "reason": "postgis_not_available"})
    assert probe.main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"


def test_default_probe_never_runs_transactional_ddl(monkeypatch, capsys):
    monkeypatch.setattr(probe.sys, "argv", ["verify_hosting_backends.py"])
    monkeypatch.setattr(probe, "load_credentials", lambda *args: {})
    monkeypatch.setattr(probe, "probe_postgres", lambda *args: {"status": "unavailable"})
    monkeypatch.setattr(probe, "probe_redis", lambda *args: {"status": "unavailable"})
    monkeypatch.setattr(probe, "probe_transactional_postgis", lambda *args: pytest.fail("DDL is opt-in only"))
    assert probe.main() == 2
    assert "postgis_transaction_test" not in json.loads(capsys.readouterr().out)


def test_persistent_extension_setup_requires_explicit_transaction_test_mode(monkeypatch):
    monkeypatch.setattr(probe.sys, "argv", ["verify_hosting_backends.py", "--enable-test-postgis"])
    monkeypatch.setattr(probe, "load_credentials", lambda *args: pytest.fail("Invalid mode must not read credentials"))
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2
