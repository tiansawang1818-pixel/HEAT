# Container architectures

The default deployment targets Linux amd64 and uses `postgis/postgis:16-3.4`. Upstream's prebuilt PostGIS image currently supports amd64 only. Do not claim that changing `platform` alone provides native ARM compatibility or deploy it through transparent emulation on a small free machine.

For an authorized, **fresh** Linux arm64 server, append `docker-compose.arm64.yml` after the base and production configuration. Its PostgreSQL image builds from official `postgres:16-bookworm` and the PostgreSQL Global Development Group's signed `postgresql-16-postgis-3` packages. The PGDG repository supports both architectures. The package supplies its current supported PostGIS 3 release; it is not necessarily the default image's 3.4 release. Record `PostGIS_Full_Version()` and the built image digest with deployment records.

```sh
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml -f docker-compose.arm64.yml config --format json | python3 scripts/check_compose_config.py
```

This command only validates configuration. No host has been allocated or started. On a real server the operator must verify credentials, resource limits and authorization first, then build native images and run deployment checks.

Never attach an existing amd64 PostgreSQL data directory to another architecture or silently change the PostGIS binaries used by an existing database. Restore a logical dump into a separate fresh database, check extension versions and row/geometry checksums, and rehearse application queries before switching traffic. Keep the original database and recovery files until the responsible operator approves the migration.

CI's `arm64` job uses the standard `ubuntu-24.04-arm` runner, with a twenty-minute timeout. It builds native database/API/Caddy images and tests actual migrations, PostGIS indexes, Redis locks, Celery publication and the scheduler against isolated synthetic fixtures without real provider access. It does not register a cloud account, run ongoing ingestion, or use CI as production hosting. The web release image is checked by the normal release job; a complete ARM deployment and capacity/backup/SSO verification remain required on the selected server.

For a private repository these runners draw from included GitHub compute usage; they are not unlimited free public-repository minutes. Keep the account's existing zero-spend block, do not add billing methods, and stop if included capacity runs out. No larger paid runners, Codespaces or scheduled hosting substitutes are required by this workflow.

Primary references: [upstream PostGIS images](https://github.com/postgis/docker-postgis), [official PostgreSQL APT repository](https://wiki.postgresql.org/wiki/Apt), [GitHub standard runner specifications and private-repository billing](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
