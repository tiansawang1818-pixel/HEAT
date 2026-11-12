# Hostless readiness issue — prepared, not sent

> **Historical draft (8 November 2026).** Superseded: the app has since been deployed on Hostless and runs at <https://heatshift.hostless.app/>. Kept for the record only.


This draft contains no passwords, connection URIs, account IDs or personal email.
It has not been posted or sent to Hostless. Only the account owner, or someone
explicitly authorized to contact support, should submit it through the signed-in
account. Do not enable a paid plan, add credit, increase storage, or replace TLS
verification while investigating.

The account owner chose not to send this message. Keep it as a draft. Since the
original investigation, the owner authorized deleting the test PostgreSQL, but
Hostless retains its storage in a recovery window until 15 November 2026,
00:29:23 Asia/Bangkok. Immediate permanent deletion is unavailable. A PostgreSQL
16 replacement has not been deployed because account-wide retained storage and
the replacement's per-resource zero quote cannot yet be reconciled. Update the
incident state before any future owner-authorized submission.

## Subject

New PostgreSQL 17 cannot pass health check within free resource pool

## Message

We are checking whether an always-on PostGIS/Celery application can run within
the account's free allowance, without a payment card or paid capacity. The
dashboard shows a shared pool of 0.5 vCPU, 1024 MiB RAM and 512 MiB database
storage. We have not deployed the application or installed repository access.

On 7 November 2026 we created a PostgreSQL 17 test database named
`heat-compatibility-check` at 0.2 vCPU / 256 MiB RAM / 256 MiB storage, with an
estimated price of USD 0.00. It did not become ready for SQL connections.

- The public endpoint shown by the dashboard closes the PostgreSQL TLS
  connection before authentication/query execution: `SSL SYSCALL error: EOF
  detected`. We use the exact dashboard hostname, port 5432, public CA roots,
  and `sslmode=verify-full`. We have not disabled certificate verification.
- Status changed through Deploying, Unhealthy and Sleeping. A health check and
  one restart did not recover it. The dashboard reported `Database restart did
  not become ready within five minutes`.
- We then requested 0.3 vCPU / 512 MiB RAM, keeping storage at 256 MiB. The
  dashboard quoted USD 0.00 and confirmed the configuration fitted the free
  pool, including our 0.2 vCPU / 256 MiB RAM / 256 MiB Redis test database.
  The update failed with `The database started but did not pass its health
  check`. We cannot establish whether all requested changes became active.
- Database operational logs say collection is not enabled. Image verification
  cannot finish while the database is not running. No application data or
  migrations have been loaded.
- Redis 7.2.16 on the same account works with verified TLS and logical databases
  0/1/2. Isolated expiring test queue data survived a dashboard restart, with a
  changed server process ID. This does not establish abrupt-crash durability.
- At the last billing check, the dashboard showed USD 0.00 and `Collecting
  usage data`. We understand this is not a final invoice or future cost cap.

Please investigate the PostgreSQL health failure and confirm:

1. Whether this PostgreSQL instance can become healthy within the free pool,
   and which resource configuration is supported without creating charges.
2. Whether managed PostgreSQL includes PostGIS and permits `CREATE EXTENSION
   postgis`. We need an actual `pg_available_extensions`/spatial-query check,
   not merely confirmation that PostgreSQL is supported.
3. For Redis, the configured AOF `appendfsync` and persistence/recovery
   guarantees. The tenant ACL denies `CONFIG GET`; `INFO persistence` reports
   AOF enabled and writes OK. `INFO memory` reports `noeviction` and
   `maxmemory=0`; please clarify cgroup memory limits and OOM behavior.

Please do not upgrade, add paid capacity or change storage on our behalf.

## Evidence

- [Redacted machine report](verification/hostless-backends-2026-11-07.json)
- [Reproduction commands and limitations](managed-hosting-checks.md)
- [Hosting constraints](hosting-constraints.md)

When submitting, attach only relevant redacted evidence. Retrieve any database
identifier support needs from the authenticated dashboard; do not put live
credentials in a ticket or repository.
