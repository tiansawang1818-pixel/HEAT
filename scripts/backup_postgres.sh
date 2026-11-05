#!/bin/sh
set -eu

# Local/Compose backup helper. Production deployments should write encrypted
# backups to a separate account/region and use the managed database mechanism.
backup_dir=${BACKUP_DIR:-./backups}
database_name=${POSTGRES_DB:-heatshift}
database_user=${POSTGRES_USER:-heatshift}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
final_path="$backup_dir/heatshift-$timestamp.dump"

umask 077
mkdir -p "$backup_dir"
temporary_path=$(mktemp "$backup_dir/.heatshift-backup.XXXXXX")
trap 'rm -f "$temporary_path"' EXIT HUP INT TERM

docker compose exec -T postgres pg_dump \
  --username "$database_user" \
  --dbname "$database_name" \
  --format custom \
  --no-owner \
  --file - > "$temporary_path"

test -s "$temporary_path"
mv "$temporary_path" "$final_path"
shasum -a 256 "$final_path" > "$final_path.sha256"
trap - EXIT HUP INT TERM
printf '%s\n' "Backup created: $final_path"
printf '%s\n' "Verify before restore: shasum -a 256 -c $final_path.sha256"
