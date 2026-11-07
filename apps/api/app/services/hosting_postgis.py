"""Bounded, rollback-only PostGIS capability test for a fresh test database.

No environmental observations, application tables, migrations, or publications
are created. Geometry/index writes use a force-rollback transaction. Enabling
the extension is a distinct, explicit persistent setup operation: rolling back
extension installation and reinstalling it can leave backend-local PostGIS
operator state referring to the previous object IDs.
"""
import json


def _extension_version(connection):
    row = connection.execute("SELECT extversion FROM pg_extension WHERE extname='postgis'").fetchone()
    return row["extversion"] if row else None


def _public_tables(connection):
    return [row["tablename"] for row in connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).fetchall()]


def _index_used(value):
    if isinstance(value, dict):
        if value.get("Index Name") == "heatshift_hosting_probe_gist":
            return True
        return any(_index_used(item) for item in value.values())
    return isinstance(value, list) and any(_index_used(item) for item in value)


def rollback_postgis_probe(connection, *, enable_extension=False):
    """Return capability evidence; refuse occupied DBs instead of altering them."""
    if not connection.autocommit:
        raise ValueError("PostGIS probe requires an idle autocommit connection")
    if connection.info.transaction_status != 0:
        raise ValueError("PostGIS probe requires no existing transaction")
    before_tables = _public_tables(connection)
    if set(before_tables) - {"spatial_ref_sys"}:
        return {"status": "blocked", "reason": "test_database_has_existing_tables", "writes_attempted": False}
    original_version = _extension_version(connection)
    available = connection.execute("SELECT default_version FROM pg_available_extensions WHERE name='postgis'").fetchone()
    if not available:
        return {"status": "blocked", "reason": "postgis_not_available", "writes_attempted": False}
    if not original_version and not enable_extension:
        return {"status": "blocked", "reason": "postgis_available_not_enabled", "writes_attempted": False}
    if connection.execute("SELECT to_regclass('pg_temp.heatshift_hosting_probe_geometry') AS relation").fetchone()["relation"]:
        return {"status": "blocked", "reason": "probe_temp_table_already_exists", "writes_attempted": False}
    if not original_version:
        # The caller explicitly opted in to setup on this fresh test DB. Commit
        # extension metadata once; do not recreate extension OIDs in a session.
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout='15s'")
            connection.execute("SET LOCAL lock_timeout='2s'")
            connection.execute("CREATE EXTENSION postgis WITH SCHEMA public")
    before_version = _extension_version(connection)
    before_tables = _public_tables(connection)
    checks = {}
    with connection.transaction(force_rollback=True):
        connection.execute("SET LOCAL statement_timeout='15s'")
        connection.execute("SET LOCAL lock_timeout='2s'")
        connection.execute("SET LOCAL idle_in_transaction_session_timeout='30s'")
        checks["postgis_version"] = _extension_version(connection)
        connection.execute("CREATE TEMP TABLE heatshift_hosting_probe_geometry (id integer PRIMARY KEY, geometry geometry(MultiPolygon,4326)) ON COMMIT DROP")
        connection.execute(
            "INSERT INTO heatshift_hosting_probe_geometry "
            "SELECT i, ST_Multi(ST_MakeEnvelope(100.5+(i%10)*0.001, 13.7+(i/10)*0.001, "
            "100.501+(i%10)*0.001, 13.701+(i/10)*0.001, 4326)) FROM generate_series(0,99) i"
        )
        connection.execute("CREATE INDEX heatshift_hosting_probe_gist ON heatshift_hosting_probe_geometry USING gist(geometry)")
        checks["geometry"] = connection.execute(
            "SELECT count(*) AS rows, bool_and(ST_IsValid(geometry)) AS all_valid, "
            "min(ST_SRID(geometry)) AS srid, max(ST_SRID(ST_Transform(geometry,3857))) AS projected_srid, "
            "max(ST_HausdorffDistance(geometry,ST_Transform(ST_Transform(geometry,3857),4326))) AS roundtrip_error_degrees "
            "FROM heatshift_hosting_probe_geometry"
        ).fetchone()
        query = "SELECT id FROM heatshift_hosting_probe_geometry WHERE ST_Intersects(geometry,ST_SetSRID(ST_Point(100.5005,13.7005),4326))"
        checks["spatial_query_ids"] = [row["id"] for row in connection.execute(query).fetchall()]
        connection.execute("SET LOCAL enable_seqscan=off")
        checks["index_plan_probe_seqscan_disabled"] = True
        plan = connection.execute("EXPLAIN (FORMAT JSON, COSTS FALSE) " + query).fetchone()
        checks["gist_query_plan_verified"] = _index_used(next(iter(plan.values())))
        encoded = connection.execute("SELECT ST_AsGeoJSON(geometry) AS geojson FROM heatshift_hosting_probe_geometry WHERE id=0").fetchone()["geojson"]
        geojson = json.loads(encoded)
        checks["geojson_roundtrip_verified"] = geojson["type"] == "MultiPolygon" and geojson["coordinates"][0][0][0] == [100.5, 13.7]
    after_version = _extension_version(connection)
    after_tables = _public_tables(connection)
    temp_gone = connection.execute("SELECT to_regclass('pg_temp.heatshift_hosting_probe_geometry') AS relation").fetchone()["relation"] is None
    rollback_verified = temp_gone and before_version == after_version and before_tables == after_tables
    geometry = checks["geometry"]
    passed = (rollback_verified and geometry["rows"] == 100 and geometry["all_valid"]
              and geometry["srid"] == 4326 and geometry["projected_srid"] == 3857
              and geometry["roundtrip_error_degrees"] < 1e-7 and checks["spatial_query_ids"] == [0]
              and checks["gist_query_plan_verified"] and checks["geojson_roundtrip_verified"])
    return {"status": "passed" if passed else "blocked", "scope": "synthetic_temporary_geometry_only",
            "checks": checks, "rollback_verified": rollback_verified,
            "extension_installed_before": bool(original_version), "extension_installed_after": bool(after_version),
            "extension_enabled_by_opt_in": not bool(original_version),
            "rollback_scope": "temporary_geometry_and_index_only",
            "application_data_published": False, "production_load_verified": False}
