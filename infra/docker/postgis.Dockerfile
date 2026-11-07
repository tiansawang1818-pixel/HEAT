# PGDG publishes these signed packages for both amd64 and arm64.
# This image is for a fresh server or a verified logical backup restore.
FROM postgres:16-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       postgresql-16-postgis-3 postgresql-16-postgis-3-scripts \
    && rm -rf /var/lib/apt/lists/*

COPY infra/postgres/init.sql /docker-entrypoint-initdb.d/10-heatshift.sql
