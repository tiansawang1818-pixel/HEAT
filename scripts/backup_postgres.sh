#!/bin/sh
set -eu

# Local/Compose backup helper. Production deployments should write encrypted
# backups to a separate account/region and use the managed database mechanism.
backup_dir=${BACKUP_DIR:-./backups}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)

umask 077
mkdir -p "$backup_dir"
temporary_path=$(mktemp "$backup_dir/.heatshift-backup.XXXXXX")
final_path="$backup_dir/heatshift-$timestamp-${temporary_path##*.}.dump"
trap 'rm -f "$temporary_path"' EXIT HUP INT TERM

if [ "${HEATSHIFT_COMPOSE_ENV:-local}" = production ]; then
  set -- --env-file "${HEATSHIFT_COMPOSE_ENV_FILE:-.env.production}" -f docker-compose.yml -f docker-compose.production.yml
else
  set --
fi
docker compose "$@" exec -T postgres sh -c \
  'exec pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format custom --no-owner --file -' > "$temporary_path"

test -s "$temporary_path"
mv "$temporary_path" "$final_path"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$final_path" > "$final_path.sha256"
else
  shasum -a 256 "$final_path" > "$final_path.sha256"
fi
trap - EXIT HUP INT TERM
printf '%s\n' "Backup created: $final_path"
printf '%s\n' "Verify before restore: shasum -a 256 -c $final_path.sha256"
