#!/bin/bash
# Create/refresh the read-only roles on the LIVE Postgres 17 and (re)generate their passwords.
# Run inside WSL:  bash /mnt/c/desarrollo/fondos/scripts/ops/create_readonly_roles.sh
# Passwords are random, written to /opt/docker/db/postgresql17/secrets/<role>_password (mode 600),
# never printed. Re-running keeps existing password files and re-applies the grants (idempotent);
# delete a password file first if you want it rotated.
set -euo pipefail
SEC=/opt/docker/db/postgresql17/secrets
SQL=/mnt/c/desarrollo/fondos/db/pg_ops/readonly_roles.sql
OWNERPW=$(cat $SEC/postgres_password)
PSQL="docker exec -i -e PGPASSWORD=$OWNERPW fondos_postgres psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U fondos_owner -d fondos"

echo "[1] grants and role settings"
$PSQL < $SQL

echo "[2] passwords"
for role in fondos_ro superset_ro; do
  f=$SEC/${role}_password
  if [ ! -s "$f" ]; then
    ( umask 077; head -c 24 /dev/urandom | base64 | tr '+/' 'AB' | tr -d '=\n' > "$f" )
    chmod 600 "$f"; echo "   $role: new password written to $f"
  else
    echo "   $role: keeping existing $f"
  fi
  # The new password goes in through stdin (not argv). The password alphabet is [A-Za-z0-9] only,
  # so single-quoting it in \set is safe. (The owner password still travels via docker exec -e,
  # which is visible to other local users of this single-user box, as in the other ops scripts.)
  { printf "\\\\set pw '%s'\n" "$(cat $f)"; printf "ALTER ROLE %s PASSWORD :'pw';\n" "$role"; } | \
    docker exec -i -e PGPASSWORD=$OWNERPW fondos_postgres psql -v ON_ERROR_STOP=1 -q -h 127.0.0.1 -U fondos_owner -d fondos
done
echo "DONE. DBeaver -> fondos_ro ; Metabase -> superset_ro ; host 127.0.0.1 port 5436 db fondos"
