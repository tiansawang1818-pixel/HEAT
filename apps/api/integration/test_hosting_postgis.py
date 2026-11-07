"""Exercise real extension installation and rollback only in disposable CI DBs."""
import os
from uuid import uuid4

import pytest

if os.environ.get("HEATSHIFT_RUN_STACK_TESTS") != "true":
    pytest.skip("Requires explicitly isolated CI PostgreSQL", allow_module_level=True)

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from app.core.config import settings
from app.db.session import engine
from app.services.hosting_postgis import rollback_postgis_probe


@pytest.fixture
def fresh_postgis_test_connection():
    assert settings.environment == "test" and engine.url.database == "heatshift_ci"
    assert engine.url.host in {"localhost", "127.0.0.1"}
    options = dict(host=engine.url.host, port=engine.url.port or 5432, user=engine.url.username,
                   password=engine.url.password, autocommit=True, row_factory=dict_row)
    name = "heatshift_postgis_probe_ci_" + uuid4().hex[:12]
    with psycopg.connect(dbname="heatshift_ci", **options) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        try:
            with psycopg.connect(dbname=name, **options) as connection:
                yield connection
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def assert_no_probe_writes(connection):
    assert connection.execute("SELECT count(*) AS n FROM pg_extension WHERE extname='postgis'").fetchone()["n"] == 0
    assert connection.execute("SELECT count(*) AS n FROM pg_tables WHERE schemaname='public'").fetchone()["n"] == 0
    assert connection.execute("SELECT to_regclass('pg_temp.heatshift_hosting_probe_geometry') AS r").fetchone()["r"] is None


def test_postgis_install_spatial_index_crs_and_geojson_are_rolled_back(fresh_postgis_test_connection):
    connection = fresh_postgis_test_connection
    assert_no_probe_writes(connection)
    result = rollback_postgis_probe(connection)
    assert result["status"] == "passed"
    assert result["rollback_verified"] and result["checks"]["gist_query_plan_verified"]
    assert result["extension_installed_before"] is False and result["extension_installed_after"] is False
    assert result["application_data_published"] is False and result["production_load_verified"] is False
    assert_no_probe_writes(connection)
    assert rollback_postgis_probe(connection)["status"] == "passed"  # Safe to repeat.


def test_postgis_probe_failure_after_inserts_still_rolls_back(fresh_postgis_test_connection):
    connection = fresh_postgis_test_connection

    class FailBeforeIndex:
        def __getattr__(self, name):
            return getattr(connection, name)

        def execute(self, query, *args, **kwargs):
            if str(query).startswith("CREATE INDEX heatshift_hosting_probe_gist"):
                raise RuntimeError("Injected CI failure after temp inserts")
            return connection.execute(query, *args, **kwargs)

    with pytest.raises(RuntimeError, match="Injected CI failure"):
        rollback_postgis_probe(FailBeforeIndex())
    assert_no_probe_writes(connection)
