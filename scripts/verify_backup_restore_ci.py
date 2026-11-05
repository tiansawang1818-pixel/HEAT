#!/usr/bin/env python3
"""Restore an ephemeral CI dump and compare every application table checksum.

Refuses other database names and existing restore databases; never drops data.
Requires an explicitly isolated GitHub Actions PostGIS service container.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    container = os.environ.get("POSTGIS_CI_CONTAINER", "")
    if os.environ.get("CI") != "true" or not re.fullmatch(r"[a-f0-9]{12,64}", container):
        raise SystemExit("Refusing restore outside an isolated CI container")

    def docker(*arguments, **kwargs):
        return subprocess.run(["docker", "exec", "-i", container, *arguments], check=True, **kwargs)

    def sql(database, query):
        return docker("psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "heatshift", "-d", database, "-At", "-c", query, capture_output=True, text=True).stdout.strip()

    exists = sql("heatshift_ci", "SELECT 1 FROM pg_database WHERE datname='heatshift_restore_ci'")
    if exists:
        raise SystemExit("Refusing to overwrite an existing restore database")
    tables = json.loads(sql("heatshift_ci", "SELECT json_agg(tablename ORDER BY tablename) FROM pg_tables WHERE schemaname='public' AND tablename<>'spatial_ref_sys'"))
    if not tables or "dataset_versions" not in tables:
        raise SystemExit("Expected migrated CI application tables are missing")

    def snapshots(database):
        rows = {}
        for table in tables:
            if not re.fullmatch(r"[a-z_]+", table):
                raise SystemExit("Unexpected table identifier")
            query = 'SELECT count(*)::text || \':\' || coalesce(md5(string_agg(to_jsonb(t)::text, chr(10) ORDER BY to_jsonb(t)::text)), \'empty\') FROM "%s" t' % table
            rows[table] = sql(database, query)
        return rows

    before = snapshots("heatshift_ci")
    with tempfile.TemporaryDirectory(prefix="heatshift-restore-ci-") as directory:
        backup = Path(directory) / "backup.dump"
        with backup.open("wb") as output:
            docker("pg_dump", "-U", "heatshift", "-d", "heatshift_ci", "-Fc", "--no-owner", stdout=output)
        assert backup.stat().st_size > 0
        docker("createdb", "-U", "heatshift", "--template=template0", "heatshift_restore_ci", capture_output=True)
        with backup.open("rb") as source:
            docker("pg_restore", "-U", "heatshift", "-d", "heatshift_restore_ci", "--no-owner", "--exit-on-error", stdin=source, capture_output=True)
    after = snapshots("heatshift_restore_ci")
    if before != after:
        raise SystemExit("Restore table counts/checksums differ from the source")
    assert sql("heatshift_restore_ci", "SELECT ST_SRID(geometry) FROM geographic_areas WHERE id='ci-central'") == "4326"
    assert sql("heatshift_restore_ci", "SELECT count(*) FROM dataset_versions WHERE status='published'") == "2"
    print("Backup restored; counts and row checksums match for %d application tables, including published versions and PostGIS geometry." % len(tables))


if __name__ == "__main__":
    main()
