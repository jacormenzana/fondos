#!/bin/bash
# Relocate the LIVE Postgres 17 (container fondos_postgres, port 5436) from the Stage-9 rehearsal
# path to /opt/docker/db/postgresql17/, and recreate it from permanent, versioned compose files.
#
# Run inside WSL:   bash /mnt/c/desarrollo/fondos/scripts/ops/relocate_live_pg17.sh
# Downtime: ~1-2 minutes (clean stop -> instant same-disk rename -> start). Pause the launchers first.
# Nothing is copied or deleted: the data directory is RENAMED (same filesystem), the container is
# recreated from docker/docker-compose.yml + docker/docker-compose.wsl.override.yml.
#
# Rollback if step 5 fails (data is intact, only its path changed):
#   docker run --rm -v /:/host --entrypoint mv postgres:17 /host/opt/docker/db/postgresql17/data /host/home/jac/fondos_stage9/pgdata
#   docker compose -p docker -f <repo>/docker/docker-compose.yml -f <stage9 override> up -d postgres
set -euo pipefail
BASE=/opt/docker/db/postgresql17
OLD=/home/jac/fondos_stage9
REPO=/mnt/c/desarrollo/fondos/docker
CF="-p docker -f $REPO/docker-compose.yml -f $REPO/docker-compose.wsl.override.yml"

echo "[0] preflight: no client connections on the live server"
PW=$(cat $OLD/secrets/postgres_password)
N=$(docker exec -e PGPASSWORD="$PW" fondos_postgres psql -h 127.0.0.1 -U fondos_owner -d fondos -Atc \
    "select count(*) from pg_stat_activity where backend_type='client backend' and pid<>pg_backend_pid()")
[ "$N" = "0" ] || { echo "ABORT: $N client connection(s) still open (pipelines/DBeaver on :5436?)"; exit 1; }

echo "[1] layout + secret"
mkdir -p $BASE/secrets $BASE/backups
cp -p $OLD/secrets/postgres_password $BASE/secrets/postgres_password
cat > $BASE/up.sh <<'UP'
#!/bin/bash
# Recreate/start the live Postgres 17 stack (see docker/docker-compose.wsl.override.yml in the repo).
R=/mnt/c/desarrollo/fondos/docker
exec docker compose -p docker -f $R/docker-compose.yml -f $R/docker-compose.wsl.override.yml up -d postgres
UP
chmod +x $BASE/up.sh

echo "[2] validate the new compose config BEFORE stopping anything"
docker compose $CF config -q && echo "   compose config OK"
docker compose $CF config | grep -E "5436|/opt/docker/db/postgresql17" | sed 's/^/   /'

echo "[3] clean stop"
docker stop -t 120 fondos_postgres >/dev/null
docker logs --tail 5 fondos_postgres 2>&1 | grep -E "shut down" | tail -1 | sed 's/^/   /'

echo "[4] rename the data dir (root helper, ONE bind mount => same-filesystem rename, no copy)"
docker run --rm -v /:/host --entrypoint mv postgres:17 /host$OLD/pgdata /host$BASE/data
docker run --rm -v $BASE:/x --entrypoint sh postgres:17 -c 'echo "   PG_VERSION=$(cat /x/data/PG_VERSION) entries=$(ls /x/data | wc -l) owner/mode=$(stat -c "%U:%G %a" /x/data)"'

echo "[5] recreate the container from the permanent files"
docker rm fondos_postgres >/dev/null
docker compose $CF up -d postgres 2>&1 | tail -3
s=none
for i in $(seq 1 24); do s=$(docker inspect -f '{{.State.Health.Status}}' fondos_postgres 2>/dev/null || echo none); [ "$s" = healthy ] && break; sleep 5; done
echo "   health after wait: $s"
docker logs --tail 3 fondos_postgres 2>&1 | sed 's/^/   /'
[ "$s" = healthy ] || { echo "NOT HEALTHY - see rollback notes at the top of this script"; exit 1; }
echo "DONE. Verify with: python -c \"from shared.db import get_connection; print(get_connection().execute('select count(*) from silver.fund_master').fetchone())\""
